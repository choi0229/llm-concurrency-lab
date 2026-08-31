#!/usr/bin/env python3
"""Phase 4 closed-model result collector (Unit 4/4.5/4.6 harness verification base; reused
unmodified for Unit 5 screening). Reads one run's raw evidence directory and writes result.json +
validity.json.

Unit 4.6 revision: separates target_concurrency (what k6 was configured to attempt) from
actual_started (what it actually managed to start) -- never assumes they're equal. Makes
completed-only client latency trends authoritative for SLO (rejected/failed durations must never
enter the SLO population). Adds server-side outcome evidence (before/after Prometheus snapshot
delta, prewarm-count-corrected) and a GREEN/AMBER/RED + failure-taxonomy classifier. See
docs/test-plan/phase4-closed-harness-protocol.md for the full frozen rationale.

Unit 4.5 revision (still in effect): separates MEASUREMENT VALIDITY (harness/environment
integrity) from MODEL OUTCOME (what the architecture actually did) -- a legitimate rejection or
timeout under real saturation does NOT invalidate a run.

Unit 5.3 revision (M3 pending-connection validity amendment, still in effect): the original Unit 5
`m3_pending_no_anomaly` check (any nonzero sample anywhere in the whole process-lifetime Prometheus
window -> INVALID) was replaced after Unit 5.2 forensics showed it flagged an ordinary, transient,
canonical-wave connection-pool ramp-up sample (1 scrape, resolved by the next scrape) that had no
observable effect on completion, reliability, or SLO. The new check
(`m3_connection_limit_binding_absent`) is wave-window-scoped and requires BOTH sustained pending
(>=SUSTAINED_PENDING_MIN_CONSECUTIVE_SAMPLES consecutive nonzero 1s samples) AND the pool's active
connections actually reaching its own configured `reactor_netty_connection_provider_max_connections`
ceiling -- i.e. evidence that the frozen WEBCLIENT_MAX_CONNECTIONS config, not ordinary transient
ramp-up, constrained the primary workload. See
docs/test-results/phase4/unit5.3-m3-pending-validity-amendment/SUMMARY.md for the full rationale
and docs/test-results/phase4/unit5.2-m3-pending-forensics/ for the forensics that motivated it.
"""
import json
import re
import sys
import csv
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from check_phase4_stall import check_stall  # noqa: E402

SAFE_CLOSED_MAX = 640
START_SPREAD_THRESHOLD_MS = 200
SLO_TTFC_P95_S = 2.0
SLO_DURATION_P95_S = 10.0
SERVER_OUTCOME_NAMES = ["completed", "rejected", "timeout", "upstream_error", "client_disconnect", "internal_error", "write_overflow"]

# Unit 5.3 pending-validity amendment (docs/test-results/phase4/unit5.3-m3-pending-validity-
# amendment/SUMMARY.md). "Sustained" operationalizes phase4-design.md section 7-1's "지속적으로
# 0보다 크면" wording as >=3 consecutive nonzero 1s-scrape samples (Prometheus scrape interval is
# frozen at 1s -- see monitoring/prometheus/prometheus-phase4.yml): one sample is the observed
# single-scrape connection-pool ramp-up transient (Unit 5.2 forensics, M3 N=400) and must not by
# itself count as sustained; TTFC is ~1s and full stream duration ~7.8s for the frozen workload, so
# 3 consecutive samples (>=2s of continuous nonzero pending) is well short of a full stream's
# lifetime while clearly exceeding a single-scrape blip -- a deliberate, evidence-grounded
# threshold, not one picked to pass any specific run.
SUSTAINED_PENDING_MIN_CONSECUTIVE_SAMPLES = 3

# Unit 5.3.2 closure: the runtime reactor_netty_connection_provider_max_connections gauge must
# match this frozen value (docs/decisions/phase4-design.md section 7-1-1 / the WEBCLIENT_MAX_CONNECTIONS
# env passed to the m3 Gateway process in run-phase4-closed-benchmark.sh) or the run is unevaluable
# -- a runtime value that disagrees with the frozen environment config is itself a safety signal,
# not something to silently trust.
M3_FROZEN_WEBCLIENT_MAX_CONNECTIONS = 800


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def metric_line(text, name):
    if text is None:
        return None
    for line in text.splitlines():
        if line.startswith(name + " ") or line.startswith(name + "{"):
            try:
                return float(line.split()[-1])
            except (IndexError, ValueError):
                return None
    return None


_TEXT_METRIC_LABEL_RE = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


def text_metric_series(text, name):
    """Unit 5.3.2 closure: unlike metric_line() (which silently returns only the first match),
    scans every line of a Prometheus text-exposition dump for `name` and returns ALL matches as
    [(labels_dict, value), ...] -- callers that need to fail closed on unexpected cardinality (a
    second series appearing) must see every line, not just the first."""
    if text is None:
        return []
    out = []
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        if line.startswith(name + "{"):
            brace_end = line.find("}")
            if brace_end == -1:
                continue
            labels = dict(_TEXT_METRIC_LABEL_RE.findall(line[len(name):brace_end + 1]))
            try:
                value = float(line[brace_end + 1:].split()[0])
            except (IndexError, ValueError):
                continue
            out.append((labels, value))
        elif line.startswith(name + " "):
            try:
                value = float(line.split()[-1])
            except (IndexError, ValueError):
                continue
            out.append(({}, value))
    return out


def gateway_outcome_snapshot(text):
    """{outcome_name: value} for every gateway_requests_total{outcome="X"} line actually present.
    Missing outcomes stay absent (not fabricated as 0) -- caller decides how to treat absence."""
    snap = {}
    if text is None:
        return snap
    for name in SERVER_OUTCOME_NAMES:
        v = metric_line(text, f'gateway_requests_total{{outcome="{name}"}}')
        if v is not None:
            snap[name] = v
    return snap


def sum_metric_lines(text, name_prefix):
    if text is None:
        return None
    total, found = 0.0, False
    for line in text.splitlines():
        if line.startswith(name_prefix) and not line.startswith("#"):
            try:
                total += float(line.split()[-1])
                found = True
            except (IndexError, ValueError):
                continue
    return total if found else None


def range_query_peak(path):
    data = load_json(path)
    if not data or data.get("status") != "success":
        return None
    result = data.get("data", {}).get("result", [])
    peak, count = None, 0
    for series in result:
        for _, v in series.get("values", []):
            try:
                fv = float(v)
            except ValueError:
                continue
            count += 1
            if peak is None or fv > peak:
                peak = fv
    return {"peak": peak, "sample_count": count} if count > 0 else None


def _single_series(data):
    """Returns (series, series_count). series is None unless exactly one series was returned --
    Unit 5.3.1 closure: never silently pick result[0] out of an unexpected multi-series response."""
    if not data or data.get("status") != "success":
        return None, 0
    result = data.get("data", {}).get("result", [])
    if len(result) == 1:
        return result[0], 1
    return None, len(result)


def m3_connection_pool_evaluation(run_dir, gw_text, wave_start_s, wave_end_s):
    """Unit 5.3.1 temporal-coupling + Unit 5.3.2 coverage/max-connections closure (docs/
    test-results/phase4/unit5.3.1-connection-limit-temporal-coupling/ and
    unit5.3.2-connection-metric-coverage-closure/SUMMARY.md). Computes wave-scoped pending/active
    stats AND a time-aligned "binding" signal -- pending>0 and active>=configured-cap observed at
    the SAME scrape timestamp -- so a sustained pending period and an unrelated later peak in
    active connections can never combine into a false connection_limit_binding=True. Every failure
    to safely evaluate this (missing series, unexpected series cardinality, mismatched pool
    identity, non-identical pending/active wave timestamp coverage, zero common wave samples,
    missing/invalid/mismatched configured cap) sets connection_limit_evaluable=False rather than
    silently defaulting to "no binding."
    """
    pending_data = load_json(run_dir / "prom-reactor_netty_connection_provider_pending_connections.json")
    active_data = load_json(run_dir / "prom-reactor_netty_connection_provider_active_connections.json")
    pending_series, pending_series_count = _single_series(pending_data)
    active_series, active_series_count = _single_series(active_data)

    def pool_identity(labels):
        return (labels.get("name"), labels.get("remote_address"), labels.get("id"))

    series_identity_match = (
        pending_series is not None and active_series is not None and
        pool_identity(pending_series.get("metric", {})) == pool_identity(active_series.get("metric", {}))
    )

    # Fail-closed max_connections read: text_metric_series() (not metric_line()) so an unexpected
    # second line is visible, not silently discarded (Unit 5.3.2 closure §9).
    max_lines = text_metric_series(gw_text, "reactor_netty_connection_provider_max_connections")
    max_series_count = len(max_lines)
    max_labels, max_value = (max_lines[0] if max_series_count == 1 else ({}, None))
    # id/name/remote_address are present on this gauge in every M3 raw artifact audited so far
    # (Unit 5.3.2 §8) -- compare only against pending's identity (already confirmed == active's).
    max_identity_match = (
        max_series_count == 1 and pending_series is not None and
        pool_identity(max_labels) == pool_identity(pending_series.get("metric", {}))
    )
    max_value_ok = (
        max_series_count == 1 and max_value is not None and max_value > 0 and
        max_value == M3_FROZEN_WEBCLIENT_MAX_CONNECTIONS
    )

    result = {
        "max_connections_configured": max_value,
        "pending_series_count": pending_series_count,
        "active_series_count": active_series_count,
        "series_identity_match": series_identity_match,
        "max_connections_series_count": max_series_count,
        "max_connections_identity_match": max_identity_match,
        "max_connections_value_ok": max_value_ok,
        "pending_wave_sample_count": None, "active_wave_sample_count": None,
        "common_wave_sample_count": None, "timestamp_alignment_ok": None,
        "connection_limit_evaluable": False,
        "active_peak_wave": None, "pending_peak_wave": None,
        "pending_nonzero_samples_wave": None, "pending_max_consecutive_samples_wave": None,
        "sustained_pending": None,
        "binding_nonzero_samples_wave": None, "binding_max_consecutive_samples_wave": None,
        "connection_limit_binding": None,
    }

    if not (pending_series_count == 1 and active_series_count == 1 and series_identity_match
            and max_identity_match and max_value_ok):
        return result

    def to_float(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    pending_map = {t: v for t, v in pending_series.get("values", [])}
    active_map = {t: v for t, v in active_series.get("values", [])}
    in_wave = lambda t: (wave_start_s is None or t >= wave_start_s) and (wave_end_s is None or t <= wave_end_s)  # noqa: E731
    pending_wave_ts = {t for t in pending_map if in_wave(t)}
    active_wave_ts = {t for t in active_map if in_wave(t)}
    common_wave_ts = sorted(pending_wave_ts & active_wave_ts)

    result["pending_wave_sample_count"] = len(pending_wave_ts)
    result["active_wave_sample_count"] = len(active_wave_ts)
    result["common_wave_sample_count"] = len(common_wave_ts)
    # Unit 5.3.2 §1/§2: exact set equality, no nearest-timestamp/interpolation tolerance -- the
    # harness's single query_range call (same start/end/step=1) for both metrics makes identical
    # grids the expected condition (confirmed from raw for all 4 existing M3 artifacts), so any
    # divergence is treated as an evaluability failure, not silently patched over.
    timestamp_alignment_ok = (pending_wave_ts == active_wave_ts)
    result["timestamp_alignment_ok"] = timestamp_alignment_ok

    connection_limit_evaluable = bool(timestamp_alignment_ok and len(common_wave_ts) > 0)
    result["connection_limit_evaluable"] = connection_limit_evaluable
    if not connection_limit_evaluable:
        return result

    max_connections_configured = max_value
    pending_peak = active_peak = None
    pending_nonzero = pending_max_run = pending_cur_run = 0
    binding_nonzero = binding_max_run = binding_cur_run = 0
    for t in common_wave_ts:
        pv, av = to_float(pending_map[t]), to_float(active_map[t])
        if pv is None or av is None:
            pending_cur_run = binding_cur_run = 0
            continue
        pending_peak = pv if pending_peak is None else max(pending_peak, pv)
        active_peak = av if active_peak is None else max(active_peak, av)
        if pv > 0:
            pending_nonzero += 1
            pending_cur_run += 1
            pending_max_run = max(pending_max_run, pending_cur_run)
        else:
            pending_cur_run = 0
        if pv > 0 and av >= max_connections_configured:
            binding_nonzero += 1
            binding_cur_run += 1
            binding_max_run = max(binding_max_run, binding_cur_run)
        else:
            binding_cur_run = 0

    result.update({
        "active_peak_wave": active_peak, "pending_peak_wave": pending_peak,
        "pending_nonzero_samples_wave": pending_nonzero, "pending_max_consecutive_samples_wave": pending_max_run,
        "sustained_pending": pending_max_run >= SUSTAINED_PENDING_MIN_CONSECUTIVE_SAMPLES,
        "binding_nonzero_samples_wave": binding_nonzero, "binding_max_consecutive_samples_wave": binding_max_run,
        "connection_limit_binding": binding_max_run >= SUSTAINED_PENDING_MIN_CONSECUTIVE_SAMPLES,
    })
    return result


def range_query_mean(path):
    data = load_json(path)
    if not data or data.get("status") != "success":
        return None
    result = data.get("data", {}).get("result", [])
    vals = [float(v) for series in result for _, v in series.get("values", []) if _is_float(v)]
    return (sum(vals) / len(vals)) if vals else None


def _is_float(v):
    try:
        float(v)
        return True
    except ValueError:
        return False


def rss_samples(path):
    if not Path(path).exists():
        return None
    rows = []
    with open(path) as f:
        for row in csv.DictReader(f):
            try:
                rows.append((int(row["epoch_s"]), int(row["rss_kib"]), float(row["pcpu"])))
            except (KeyError, ValueError):
                continue
    if not rows:
        return None
    rss_vals = [r[1] for r in rows]
    return {"count": len(rows), "start_kib": rss_vals[0], "peak_kib": max(rss_vals), "delta_kib": max(rss_vals) - rss_vals[0]}


def k6_trend(summary, name, field="p(95)"):
    m = (summary or {}).get("metrics", {}).get(name)
    return m.get(field) if m else None


def k6_counter(summary, name):
    m = (summary or {}).get("metrics", {}).get(name)
    return int(m.get("count", 0)) if m else 0


def classify(reliability_pass, slo_pass, completed, server_outcomes_wave):
    """Returns (color, classification, evidence[]). Conservative: never asserts a root cause
    without server-side evidence (docs/test-plan/phase4-design.md Unit 4.6 brief section 15/24)."""
    if reliability_pass:
        if slo_pass:
            return "GREEN", "GREEN", ["reliability PASS, SLO PASS"]
        return "AMBER", "AMBER", [f"reliability PASS but SLO violated (completed TTFC/duration p95 outside {SLO_TTFC_P95_S}s/{SLO_DURATION_P95_S}s)"]

    ev = []
    rejected = server_outcomes_wave.get("rejected", 0) or 0
    timeout = server_outcomes_wave.get("timeout", 0) or 0
    upstream_error = server_outcomes_wave.get("upstream_error", 0) or 0
    client_disconnect = server_outcomes_wave.get("client_disconnect", 0) or 0
    internal_error = server_outcomes_wave.get("internal_error", 0) or 0

    if rejected > 0:
        ev.append(f"server outcome=rejected count={rejected} (M1 AbortPolicy or equivalent admission ceiling)")
        return "RED", "MODEL_REJECTION", ev
    if timeout > 0:
        ev.append(f"server outcome=timeout count={timeout} (Gateway absolute-deadline evidence)")
        return "RED", "MODEL_TIMEOUT", ev
    if client_disconnect > 0:
        ev.append(f"server outcome=client_disconnect count={client_disconnect} -- UNEXPECTED for a workload with no intentional disconnect; requires investigation, not auto-classified as normal model behavior")
        return "RED", "UNEXPECTED_CLIENT_DISCONNECT", ev
    if upstream_error > 0:
        ev.append(f"server outcome=upstream_error count={upstream_error} -- cause not confirmed from this evidence alone (could be DOWNSTREAM_LIMIT, control problem, or genuine model saturation); Mock/control corroboration required before asserting a specific taxonomy label")
        return "RED", "UPSTREAM_ERROR_UNCONFIRMED_CAUSE", ev
    if internal_error > 0:
        ev.append(f"server outcome=internal_error count={internal_error} -- this is a bug signal, already caught by validity gate (internal_error_eq_0), should not reach classification as a model outcome")
        return "RED", "INTERNAL_ERROR_BUG", ev

    ev.append("client-side reliability failure observed (non-completed terminal outcomes) but no corresponding server-side outcome evidence found")
    return "RED", "UNCLASSIFIED_MODEL_FAILURE", ev


def main():
    if len(sys.argv) != 2:
        print("usage: collect_phase4_closed_result.py <run_dir>", file=sys.stderr)
        sys.exit(2)
    run_dir = Path(sys.argv[1])

    env = load_json(run_dir / "environment.json") or {}
    ts = load_json(run_dir / "timestamps.json") or {}
    k6 = load_json(run_dir / "k6-summary.json") or {}
    gw_before_text = (run_dir / "gateway-metrics-before-wave.txt").read_text() if (run_dir / "gateway-metrics-before-wave.txt").exists() else None
    gw_text = (run_dir / "gateway-metrics-final.txt").read_text() if (run_dir / "gateway-metrics-final.txt").exists() else None
    mock_text = (run_dir / "mock-metrics-final.txt").read_text() if (run_dir / "mock-metrics-final.txt").exists() else None
    pid_verif = (run_dir / "pid-verification.txt").read_text() if (run_dir / "pid-verification.txt").exists() else ""
    k6_exit = int((run_dir / "k6-exit-code.txt").read_text().strip()) if (run_dir / "k6-exit-code.txt").exists() else -1

    model = env.get("model")
    target_concurrency = env.get("concurrency")
    prewarm_count = env.get("prewarm_count", 5)

    # ---- Client cohort: target vs actual_started (Unit 4.6 section 1) ----
    actual_started = k6_counter(k6, "client_actual_started_total")
    completed = k6_counter(k6, "client_stream_completed_total")
    rejected = k6_counter(k6, "client_rejected_total")
    failed_mid = k6_counter(k6, "client_failed_mid_stream_total")
    failed_noevt = k6_counter(k6, "client_failed_no_event_total")
    terminal_sum = completed + rejected + failed_mid + failed_noevt
    target_reached = (actual_started == target_concurrency)
    invariant_ok = (terminal_sum == actual_started)  # NOT target_concurrency -- section 1
    dropped = k6_counter(k6, "dropped_iterations")
    reliability_pass = (completed == actual_started) and target_reached

    client_cohort = {
        "target_concurrency": target_concurrency, "actual_started": actual_started, "target_reached": target_reached,
        "completed": completed, "rejected": rejected, "failed_mid_stream": failed_mid, "failed_no_event": failed_noevt,
        "terminal_sum": terminal_sum, "invariant_ok": invariant_ok, "dropped_iterations": dropped,
    }

    # ---- Client latency: completed-only authoritative, diagnostic all-outcome kept separately ----
    completed_ttfc_p95 = k6_trend(k6, "client_completed_ttfc_seconds") if completed > 0 else None
    completed_ttfc_avg = k6_trend(k6, "client_completed_ttfc_seconds", "avg") if completed > 0 else None
    completed_dur_p95 = k6_trend(k6, "client_completed_stream_duration_seconds") if completed > 0 else None
    completed_dur_avg = k6_trend(k6, "client_completed_stream_duration_seconds", "avg") if completed > 0 else None
    slo_pass = (completed > 0 and completed_ttfc_p95 is not None and completed_dur_p95 is not None and
                completed_ttfc_p95 <= SLO_TTFC_P95_S and completed_dur_p95 <= SLO_DURATION_P95_S)

    client_latency = {
        "completed_ttfc_p95_s": completed_ttfc_p95, "completed_ttfc_avg_s": completed_ttfc_avg,
        "completed_duration_p95_s": completed_dur_p95, "completed_duration_avg_s": completed_dur_avg,
        "diagnostic_all_outcomes_ttfc_p95_s": k6_trend(k6, "client_ttfc_seconds"),
        "diagnostic_all_outcomes_duration_p95_s": k6_trend(k6, "client_stream_duration_seconds"),
        "slo_ttfc_threshold_s": SLO_TTFC_P95_S, "slo_duration_threshold_s": SLO_DURATION_P95_S,
    }

    wave_start = (k6.get("metrics", {}) or {}).get("client_wave_start_epoch_ms", {})
    spread_ms = (wave_start["max"] - wave_start["min"]) if (wave_start.get("min") is not None and wave_start.get("max") is not None) else None
    wave_start_spread = {"spread_ms": spread_ms, "exceeds_threshold": (spread_ms is not None and spread_ms >= START_SPREAD_THRESHOLD_MS)}

    # ---- Server-side outcome evidence: before/after delta, prewarm-corrected (section 12) ----
    before_snap = gateway_outcome_snapshot(gw_before_text)
    after_snap = gateway_outcome_snapshot(gw_text)
    delta = {name: (after_snap[name] - before_snap.get(name, 0.0)) for name in after_snap if name in after_snap}
    server_wave_completed_estimate = delta.get("completed", 0.0) - prewarm_count
    prewarm_contamination_ok = (int(round(server_wave_completed_estimate)) == completed)
    server_outcomes_wave = dict(delta)
    if "completed" in server_outcomes_wave:
        server_outcomes_wave["completed"] = server_wave_completed_estimate

    # ---- Resource metrics ----
    platform_threads = range_query_peak(run_dir / "prom-jvm_threads_live_threads.json")
    cpu_mean = range_query_mean(run_dir / "prom-process_cpu_usage.json")
    cpu_peak = range_query_peak(run_dir / "prom-process_cpu_usage.json")
    sys_cpu_count = range_query_peak(run_dir / "prom-system_cpu_count.json")
    ncpu = sys_cpu_count["peak"] if sys_cpu_count else None
    cpu = {
        "process_cpu_usage_mean": cpu_mean, "process_cpu_usage_peak": cpu_peak["peak"] if cpu_peak else None,
        "system_cpu_count": ncpu,
        "avg_cores_estimate": (cpu_mean * ncpu) if (cpu_mean is not None and ncpu is not None) else None,
        "peak_cores_estimate_diagnostic": (cpu_peak["peak"] * ncpu) if (cpu_peak and ncpu is not None) else None,
    }
    rss = rss_samples(run_dir / "rss-samples.csv")
    fd_open = range_query_peak(run_dir / "prom-process_files_open_files.json")
    fd_max = range_query_peak(run_dir / "prom-process_files_max_files.json")
    fd = {"open_peak": fd_open["peak"] if fd_open else None, "max_files": fd_max["peak"] if fd_max else None, "metric_present": fd_open is not None}
    heap = range_query_peak(run_dir / "prom-jvm_memory_used_bytes.json")

    common_gateway = {
        "gateway_active_requests_final": metric_line(gw_text, "gateway_active_requests"),
        "gateway_upstream_active_final": metric_line(gw_text, "gateway_upstream_active"),
        "internal_error_total": after_snap.get("internal_error"),
    }

    implementation_specific = {}
    m3_connection_limit_evaluable = True   # trivially true for m1/m2 -- the check does not apply
    m3_connection_limit_binding = False
    write_overflow_total = None
    if model == "m1":
        write_overflow_total = sum_metric_lines(gw_text, "servlet_write_overflow_total")
        implementation_specific = {
            "executor_active_peak": range_query_peak(run_dir / "prom-executor_active.json"),
            "executor_queue_depth_peak": range_query_peak(run_dir / "prom-executor_queue_depth.json"),
            "executor_rejected_final": metric_line(gw_text, "executor_rejected_total"),
        }
    elif model == "m2":
        write_overflow_total = sum_metric_lines(gw_text, "servlet_write_overflow_total")
        implementation_specific = {
            "virtual_tasks_active_peak": range_query_peak(run_dir / "prom-virtual_tasks_active.json"),
            "virtual_tasks_started_final": metric_line(gw_text, "virtual_tasks_started_total"),
        }
    elif model == "m3":
        # Unit 5.3 pending-validity amendment + Unit 5.3.1 temporal-coupling closure. Wave window =
        # [first client_wave_start_epoch_ms, k6_wall_end_ms] -- both already-recorded fields
        # (k6-summary.json / timestamps.json), no new instrumentation and no manufactured margin
        # (Unit 5.2 forensics confirmed a plain numeric-timestamp comparison against these two
        # existing fields correctly separates prewarm/drain from the canonical wave despite the 1s
        # scrape grid vs ms-precision client timestamps). Lifetime (process-start-to-drain-end)
        # peaks are kept as diagnostics only -- never used for validity.
        wave_start_ms = wave_start.get("min")
        wave_end_ms = ts.get("k6_wall_end_ms")
        wave_start_s = (wave_start_ms / 1000) if wave_start_ms is not None else None
        wave_end_s = (wave_end_ms / 1000) if wave_end_ms is not None else None

        pending_lifetime = range_query_peak(run_dir / "prom-reactor_netty_connection_provider_pending_connections.json")
        active_lifetime = range_query_peak(run_dir / "prom-reactor_netty_connection_provider_active_connections.json")
        pool = m3_connection_pool_evaluation(run_dir, gw_text, wave_start_s, wave_end_s)

        m3_connection_limit_evaluable = pool["connection_limit_evaluable"]
        # Unevaluable is a distinct failure from "evaluated and found absent" -- never collapse a
        # missing/malformed safety signal into a silent binding=False pass (Unit 5.3.1 closure §5).
        m3_connection_limit_binding = bool(pool["connection_limit_binding"]) if m3_connection_limit_evaluable else None
        implementation_specific = {
            "reactor_connection_active_peak": active_lifetime,
            "reactor_connection_pending_peak": pending_lifetime,
            "reactor_connection_pool": pool,
        }

    mock = {
        "active_final": metric_line(mock_text, "mockllm_active_requests"),
        "waiting_final": metric_line(mock_text, "mockllm_waiting_requests"),
        "completed_final": metric_line(mock_text, "mockllm_completed_requests_total"),
    }

    postflight_clean = (
        common_gateway["gateway_active_requests_final"] in (0.0, 0, None) and
        common_gateway["gateway_upstream_active_final"] in (0.0, 0, None) and
        mock["active_final"] in (0.0, 0, None)
    )
    pid_match = "PID_MATCH=true" in pid_verif
    stall = check_stall(run_dir)

    # ---- MEASUREMENT VALIDITY (harness/environment integrity only -- NOT model outcome) ----
    validity = {
        "target_reached": target_reached,
        "cohort_invariant_ok": invariant_ok,
        "dropped_eq_0": dropped == 0,
        "k6_exit_zero": k6_exit == 0,
        "pid_match": pid_match,
        "rss_samples_valid": rss is not None and rss["count"] > 0,
        "fd_metric_present": fd["metric_present"],
        "cpu_metric_parsable": cpu["avg_cores_estimate"] is not None,
        "prometheus_sampling_valid": platform_threads is not None and platform_threads["sample_count"] > 0,
        "start_spread_ok": not wave_start_spread["exceeds_threshold"],
        "internal_error_eq_0": (common_gateway["internal_error_total"] in (0.0, 0, None)),
        "write_overflow_eq_0": (write_overflow_total in (0.0, 0, None) or model == "m3"),
        "m3_connection_metrics_evaluable": m3_connection_limit_evaluable,
        # When unevaluable, m3_connection_limit_binding is None -- treated here as "not confirmed
        # present" (vacuous pass) rather than fabricated False, because the separate
        # m3_connection_metrics_evaluable key above is what actually flags this run invalid; this
        # key must never independently claim binding was ruled out when it wasn't measured
        # (Unit 5.3.1 closure §5/§10).
        "m3_connection_limit_binding_absent": not m3_connection_limit_binding if m3_connection_limit_binding is not None else True,
        "prewarm_contamination_ok": prewarm_contamination_ok,
        "postflight_clean": postflight_clean,
        "environment_stall_false": not stall["stall_detected"],
        "control_range_ok": (target_concurrency is not None and target_concurrency <= SAFE_CLOSED_MAX),
    }
    valid = all(validity.values())
    invalid_reasons = [k for k, v in validity.items() if not v]

    # ---- MODEL OUTCOME (diagnostic/result classification -- does NOT affect validity) ----
    color, classification, evidence = classify(reliability_pass, slo_pass, completed, server_outcomes_wave)
    model_outcome = {
        "actual_started": actual_started, "completed": completed, "rejected": rejected,
        "failed_mid_stream": failed_mid, "failed_no_event": failed_noevt,
        "reliability_pass": reliability_pass, "slo_pass": (slo_pass if reliability_pass else None),
        "server_outcomes_wave_estimate": server_outcomes_wave,
        "prewarm_contamination_ok": prewarm_contamination_ok,
        "color": color, "classification": classification, "classification_evidence": evidence,
    }

    result = {
        "valid": valid, "invalid_reasons": invalid_reasons,
        "model": model, "concurrency": target_concurrency, "run_label": env.get("run_label"),
        "client_cohort": client_cohort, "model_outcome": model_outcome, "client_latency": client_latency,
        "wave_start_spread": wave_start_spread, "platform_threads": platform_threads, "cpu": cpu, "rss": rss,
        "fd": fd, "heap": heap, "common_gateway": common_gateway, "implementation_specific": implementation_specific,
        "mock": mock, "postflight_clean": postflight_clean, "stall_check": stall, "timestamps": ts, "environment": env,
    }

    (run_dir / "result.json").write_text(json.dumps(result, indent=2))
    (run_dir / "validity.json").write_text(json.dumps({"valid": valid, "checks": validity, "invalid_reasons": invalid_reasons}, indent=2))
    print(json.dumps({"valid": valid, "invalid_reasons": invalid_reasons, "color": color, "classification": classification}, indent=2))


if __name__ == "__main__":
    main()
