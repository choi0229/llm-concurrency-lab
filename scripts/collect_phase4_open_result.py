#!/usr/bin/env python3
"""Phase 4 Part B -- Open-model (constant-arrival-rate) result collector. Reads one run's raw
evidence directory (scripts/run-phase4-open-benchmark.sh output) and writes result.json +
validity.json. Client-cohort-authoritative (docs/decisions/phase2-formal-linux-environment.md) --
k6's own measurement-phase counters are the cohort source of truth; Gateway/Prometheus data is
diagnostic (backlog, resource) or whole-process-scoped evidence (server outcome taxonomy hints),
never forced into exact equality with the client cohort. See
docs/test-plan/phase4-open-screening-protocol.md for full rationale.
"""
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from check_phase4_stall import check_stall  # noqa: E402 -- generic, reused unchanged for Open too

SAFE_OPEN_MAX = 256
SLO_TTFC_P95_S = 2.0
SLO_DURATION_P95_S = 10.0
MSAR_THROUGHPUT_RATIO_MIN = 0.98
SERVER_OUTCOME_NAMES = ["completed", "rejected", "timeout", "upstream_error", "client_disconnect", "internal_error", "write_overflow"]


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
    snap = {}
    if text is None:
        return snap
    for name in SERVER_OUTCOME_NAMES:
        v = metric_line(text, f'gateway_requests_total{{outcome="{name}"}}')
        if v is not None:
            snap[name] = v
    return snap


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


def range_query_mean(path):
    data = load_json(path)
    if not data or data.get("status") != "success":
        return None
    result = data.get("data", {}).get("result", [])
    vals = []
    for series in result:
        for _, v in series.get("values", []):
            try:
                vals.append(float(v))
            except ValueError:
                continue
    return (sum(vals) / len(vals)) if vals else None


def backlog_trend(path, measurement_start_s, measurement_end_s):
    """Second-half vs first-half mean of the measurement window -- qualitative rising/flat/falling
    signal only (docs/test-plan/phase4-open-screening-protocol.md section 2.3: exact slope
    threshold deliberately NOT frozen yet)."""
    data = load_json(path)
    if not data or data.get("status") != "success":
        return None
    result = data.get("data", {}).get("result", [])
    if not result:
        return None
    values = sorted(result[0].get("values", []), key=lambda tv: tv[0])
    window = [(t, float(v)) for t, v in values if measurement_start_s <= t <= measurement_end_s]
    if len(window) < 4:
        return {"available": False, "sample_count": len(window)}
    mid = len(window) // 2
    first_half = [v for _, v in window[:mid]]
    second_half = [v for _, v in window[mid:]]
    first_mean = sum(first_half) / len(first_half)
    second_mean = sum(second_half) / len(second_half)
    return {
        "available": True, "sample_count": len(window),
        "first_half_mean": first_mean, "second_half_mean": second_mean,
        "rising": second_mean > first_mean * 1.10,  # qualitative-only, not a frozen classification threshold
    }


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


def classify(reliability_pass, slo_pass, throughput_ok, backlog_ok, server_outcomes):
    if reliability_pass and slo_pass and throughput_ok and backlog_ok:
        return "GREEN", "GREEN", ["reliability PASS, SLO PASS, throughput>=98% arrival, no backlog growth"]
    if reliability_pass:
        reasons = []
        if not slo_pass:
            reasons.append(f"SLO violated (completed TTFC/duration p95 outside {SLO_TTFC_P95_S}s/{SLO_DURATION_P95_S}s)")
        if not throughput_ok:
            reasons.append(f"completion throughput below {MSAR_THROUGHPUT_RATIO_MIN*100:.0f}% of actual arrival rate")
        if not backlog_ok:
            reasons.append("backlog/queue growth trend observed in measurement second half")
        return "AMBER", "AMBER", reasons or ["reliability PASS but sustainability condition failed"]

    ev = []
    rejected = server_outcomes.get("rejected", 0) or 0
    timeout = server_outcomes.get("timeout", 0) or 0
    if rejected > 0 and timeout == 0:
        ev.append(f"server outcome=rejected count={rejected} (whole-process evidence)")
        return "RED", "MODEL_REJECTION", ev
    if timeout > 0 and rejected == 0:
        ev.append(f"server outcome=timeout count={timeout} (whole-process evidence)")
        return "RED", "MODEL_TIMEOUT", ev
    if rejected > 0 and timeout > 0:
        ev.append(f"both rejected={rejected} and timeout={timeout} observed (whole-process evidence) -- coexisting signals")
        return "RED", "MODEL_REJECTION", ev
    ev.append("client-side reliability failure observed but no corresponding whole-process server outcome evidence found")
    return "RED", "UNCLASSIFIED_MODEL_FAILURE", ev


def main():
    if len(sys.argv) != 2:
        print("usage: collect_phase4_open_result.py <run_dir>", file=sys.stderr)
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
    target_rate = env.get("target_rate")
    warmup_sec = env.get("warmup_sec")
    measurement_sec = env.get("measurement_sec")

    # ---- Client cohort (measurement-phase only, per 05-phase4-open-arrival-rate.js's own phase split) ----
    actual_started = k6_counter(k6, "measurement_iterations_started_total")
    completed = k6_counter(k6, "client_completed_total")
    rejected = k6_counter(k6, "client_rejected_total")
    failed_mid = k6_counter(k6, "client_failed_mid_stream_total")
    failed_noevt = k6_counter(k6, "client_failed_no_event_total")
    terminal_sum = completed + rejected + failed_mid + failed_noevt
    invariant_ok = (terminal_sum == actual_started)
    dropped = k6_counter(k6, "dropped_iterations")
    reliability_pass = (completed == actual_started) and actual_started > 0

    actual_started_rate = (actual_started / measurement_sec) if measurement_sec else None
    completion_throughput = (completed / measurement_sec) if measurement_sec else None
    completion_ratio = (completion_throughput / actual_started_rate) if (actual_started_rate and completion_throughput is not None and actual_started_rate > 0) else None
    throughput_ok = (completion_ratio is not None and completion_ratio >= MSAR_THROUGHPUT_RATIO_MIN)

    client_cohort = {
        "target_rate": target_rate, "actual_started": actual_started, "actual_started_rate": actual_started_rate,
        "completed": completed, "rejected": rejected, "failed_mid_stream": failed_mid, "failed_no_event": failed_noevt,
        "terminal_sum": terminal_sum, "invariant_ok": invariant_ok, "dropped_iterations": dropped,
        "completion_throughput": completion_throughput, "completion_ratio": completion_ratio, "throughput_sustainability_pass": throughput_ok,
    }

    # ---- Client latency: completed-only authoritative (this fork's added client_completed_stream_duration_seconds) ----
    completed_ttfc_p95 = k6_trend(k6, "client_ttfc_completed_seconds") if completed > 0 else None
    completed_dur_p95 = k6_trend(k6, "client_completed_stream_duration_seconds") if completed > 0 else None
    slo_pass = (completed > 0 and completed_ttfc_p95 is not None and completed_dur_p95 is not None and
                completed_ttfc_p95 <= SLO_TTFC_P95_S and completed_dur_p95 <= SLO_DURATION_P95_S)

    # ---- Clock/measurement-integrity cross-check (protocol section 2.4) ----
    builtin_http_req_avg = k6_trend(k6, "http_req_duration", "avg")
    builtin_http_req_avg_s = (builtin_http_req_avg / 1000.0) if builtin_http_req_avg is not None else None
    clock_integrity_ok = True
    clock_integrity_note = "insufficient data"
    if completed_dur_p95 is not None and builtin_http_req_avg_s is not None and builtin_http_req_avg_s > 0:
        ratio = completed_dur_p95 / builtin_http_req_avg_s
        clock_integrity_ok = 0.5 <= ratio <= 2.0
        clock_integrity_note = f"custom completed_duration_p95={completed_dur_p95:.3f}s vs builtin http_req_duration avg={builtin_http_req_avg_s:.3f}s, ratio={ratio:.2f}"

    client_latency = {
        "completed_ttfc_p95_s": completed_ttfc_p95, "completed_duration_p95_s": completed_dur_p95,
        "diagnostic_all_outcomes_ttfc_p95_s": k6_trend(k6, "client_ttfc_seconds"),
        "diagnostic_all_outcomes_duration_p95_s": k6_trend(k6, "client_stream_duration_seconds"),
        "builtin_http_req_duration_avg_s": builtin_http_req_avg_s,
        "slo_ttfc_threshold_s": SLO_TTFC_P95_S, "slo_duration_threshold_s": SLO_DURATION_P95_S,
        "clock_integrity_ok": clock_integrity_ok, "clock_integrity_note": clock_integrity_note,
    }

    # ---- Measurement window (authoritative: k6's own measurement_start_epoch_s Gauge) ----
    measurement_start_s = k6_trend(k6, "measurement_start_epoch_s", "value")
    if measurement_start_s is None:
        m = (k6.get("metrics", {}) or {}).get("measurement_start_epoch_s", {})
        measurement_start_s = m.get("value") if m else None
    k6_wall_end_s = (ts.get("k6_wall_end_ms") / 1000) if ts.get("k6_wall_end_ms") is not None else None
    measurement_end_s = k6_wall_end_s

    # ---- Server outcome evidence: whole-process (warmup+measurement) snapshot delta -- disclosed as such, not measurement-phase-exact ----
    before_snap = gateway_outcome_snapshot(gw_before_text)
    after_snap = gateway_outcome_snapshot(gw_text)
    server_outcomes_whole_process = {name: (after_snap[name] - before_snap.get(name, 0.0)) for name in after_snap if name in after_snap}

    # ---- Backlog (diagnostic, qualitative only) ----
    backlog = {}
    backlog_ok = True
    if measurement_start_s is not None and measurement_end_s is not None:
        if model == "m1":
            bt = backlog_trend(run_dir / "prom-executor_queue_depth.json", measurement_start_s, measurement_end_s)
            backlog["executor_queue_depth"] = bt
        elif model == "m2":
            bt = backlog_trend(run_dir / "prom-virtual_tasks_active.json", measurement_start_s, measurement_end_s)
            backlog["virtual_tasks_active"] = bt
        else:
            bt = backlog_trend(run_dir / "prom-reactor_netty_connection_provider_active_connections.json", measurement_start_s, measurement_end_s)
            backlog["reactor_connection_active"] = bt
        bt_gw = backlog_trend(run_dir / "prom-gateway_active_requests.json", measurement_start_s, measurement_end_s)
        backlog["gateway_active_requests"] = bt_gw
        for b in backlog.values():
            if b and b.get("available") and b.get("rising"):
                backlog_ok = False

    # ---- Resource metrics ----
    platform_threads = range_query_peak(run_dir / "prom-jvm_threads_live_threads.json")
    cpu_mean = range_query_mean(run_dir / "prom-process_cpu_usage.json")
    sys_cpu_count = range_query_peak(run_dir / "prom-system_cpu_count.json")
    ncpu = sys_cpu_count["peak"] if sys_cpu_count else None
    cpu = {"process_cpu_usage_mean": cpu_mean, "system_cpu_count": ncpu,
           "avg_cores_estimate": (cpu_mean * ncpu) if (cpu_mean is not None and ncpu is not None) else None}
    rss = rss_samples(run_dir / "rss-samples.csv")
    fd_open = range_query_peak(run_dir / "prom-process_files_open_files.json")
    fd_max = range_query_peak(run_dir / "prom-process_files_max_files.json")
    fd = {"open_peak": fd_open["peak"] if fd_open else None, "max_files": fd_max["peak"] if fd_max else None, "metric_present": fd_open is not None}

    common_gateway = {
        "gateway_active_requests_final": metric_line(gw_text, "gateway_active_requests"),
        "gateway_upstream_active_final": metric_line(gw_text, "gateway_upstream_active"),
        "internal_error_total": after_snap.get("internal_error"),
    }

    # Phase 4 Open Unit 7.3 (docs/decisions/phase4-open-tomcat-connector-headroom.md): M1/M2 both
    # run on Tomcat and both now set SERVER_TOMCAT_MAX_CONNECTIONS explicitly (OPEN_TOMCAT_MAX_
    # CONNECTIONS=9600) rather than leaving Spring Boot's untuned maxConnections=8192 default in
    # effect -- the exact config gap that caused M2 R=160's original UNCLASSIFIED_MODEL_FAILURE
    # (docs/test-results/phase4/unit7.2-r160-frontdoor-isolation/). tomcat_connections_non_binding
    # re-derives that same check for every future M1/M2 run automatically, instead of requiring a
    # manual forensic investigation each time this ceiling is approached again.
    tomcat_connections_peak_info = None
    tomcat_connections_non_binding = True
    configured_max_connections = env.get("server_tomcat_max_connections")
    if model in ("m1", "m2"):
        tomcat_connections_peak_info = range_query_peak(run_dir / "prom-tomcat_connections_current_connections.json")
        if tomcat_connections_peak_info and configured_max_connections:
            # >=99% of the configured ceiling counts as "binding" -- a plateau at or essentially at
            # the ceiling (as seen in the Unit 7.2 diagnostic: sustained exactly at 8192.0) is the
            # signature; requiring an exact match would miss a near-ceiling plateau that never quite
            # touches the integer ceiling due to scrape-timing granularity.
            tomcat_connections_non_binding = tomcat_connections_peak_info["peak"] < (configured_max_connections * 0.99)

    implementation_specific = {}
    if model == "m1":
        implementation_specific = {
            "executor_active_peak": range_query_peak(run_dir / "prom-executor_active.json"),
            "executor_queue_depth_peak": range_query_peak(run_dir / "prom-executor_queue_depth.json"),
            "executor_rejected_final": metric_line(gw_text, "executor_rejected_total"),
            "tomcat_connections_current_peak": tomcat_connections_peak_info,
            "tomcat_connections_configured_max": configured_max_connections,
        }
    elif model == "m2":
        implementation_specific = {
            "virtual_tasks_active_peak": range_query_peak(run_dir / "prom-virtual_tasks_active.json"),
            "virtual_tasks_started_final": metric_line(gw_text, "virtual_tasks_started_total"),
            "tomcat_connections_current_peak": tomcat_connections_peak_info,
            "tomcat_connections_configured_max": configured_max_connections,
        }
    else:
        max_lines = text_metric_series(gw_text, "reactor_netty_connection_provider_max_connections")
        max_value = max_lines[0][1] if len(max_lines) == 1 else None
        implementation_specific = {
            "reactor_connection_active_peak": range_query_peak(run_dir / "prom-reactor_netty_connection_provider_active_connections.json"),
            "reactor_connection_pending_peak": range_query_peak(run_dir / "prom-reactor_netty_connection_provider_pending_connections.json"),
            "max_connections_configured": max_value,
            "max_connections_series_count": len(max_lines),
        }

    mock = {
        "active_final": metric_line(mock_text, "mockllm_active_requests"),
        "waiting_final": metric_line(mock_text, "mockllm_waiting_requests"),
        "completed_final": metric_line(mock_text, "mockllm_completed_requests_total"),
    }

    postflight_clean = (
        common_gateway["gateway_active_requests_final"] in (0.0, 0, None) and
        common_gateway["gateway_upstream_active_final"] in (0.0, 0, None) and
        mock["active_final"] in (0.0, 0, None) and mock["waiting_final"] in (0.0, 0, None)
    )
    pid_match = "PID_MATCH=true" in pid_verif
    stall = check_stall(run_dir)

    # REVERTED (Phase 4 Open Unit 7.1 review): dropped_iterations>0 always invalidates the run,
    # regardless of reliability_pass. A prior version of this collector treated
    # `dropped==0 or not reliability_pass` as valid, reasoning that a reliability failure alongside
    # dropped_iterations was corroborating RED evidence rather than a loadgen artifact. That
    # reasoning is withdrawn: in a constant-arrival-rate design, dropped_iterations means k6 itself
    # failed to generate the intended offered load R -- a control/loadgen signal, not a model
    # signal. Even if the model also fails under the (lower, actually-delivered) load, the run
    # cannot stand as a canonical measurement of the model's behavior AT TARGET RATE R, because R
    # was never actually offered. Canonical Open run validity requires dropped_iterations==0,
    # unconditionally.
    dropped_ok = (dropped == 0)
    validity = {
        "actual_started_generated": actual_started > 0 and k6_exit == 0,
        "cohort_invariant_ok": invariant_ok,
        "dropped_eq_0": dropped_ok,
        "k6_exit_zero": k6_exit == 0,
        "pid_match": pid_match,
        "rss_samples_valid": rss is not None and rss["count"] > 0,
        "fd_metric_present": fd["metric_present"],
        "cpu_metric_parsable": cpu["avg_cores_estimate"] is not None,
        "prometheus_sampling_valid": platform_threads is not None and platform_threads["sample_count"] > 0,
        "internal_error_eq_0": (common_gateway["internal_error_total"] in (0.0, 0, None)),
        "postflight_clean": postflight_clean,
        "environment_stall_false": not stall["stall_detected"],
        "control_range_ok": (target_rate is not None and target_rate <= SAFE_OPEN_MAX),
        "clock_integrity_ok": clock_integrity_ok,
        "measurement_window_available": measurement_start_s is not None and measurement_end_s is not None,
        "tomcat_connections_non_binding": tomcat_connections_non_binding,
    }
    valid = all(validity.values())
    invalid_reasons = [k for k, v in validity.items() if not v]

    color, classification, evidence = classify(reliability_pass, slo_pass, throughput_ok, backlog_ok, server_outcomes_whole_process)
    model_outcome = {
        "actual_started": actual_started, "completed": completed, "rejected": rejected,
        "failed_mid_stream": failed_mid, "failed_no_event": failed_noevt,
        "reliability_pass": reliability_pass, "slo_pass": (slo_pass if reliability_pass else None),
        "throughput_sustainability_pass": throughput_ok, "backlog_sustainability_pass": backlog_ok,
        "server_outcomes_whole_process_estimate": server_outcomes_whole_process,
        "color": color, "classification": classification, "classification_evidence": evidence,
    }

    result = {
        "valid": valid, "invalid_reasons": invalid_reasons,
        "model": model, "target_rate": target_rate, "run_label": env.get("run_label"),
        "client_cohort": client_cohort, "model_outcome": model_outcome, "client_latency": client_latency,
        "backlog": backlog, "platform_threads": platform_threads, "cpu": cpu, "rss": rss, "fd": fd,
        "common_gateway": common_gateway, "implementation_specific": implementation_specific,
        "mock": mock, "postflight_clean": postflight_clean, "stall_check": stall,
        "measurement_window": {"start_s": measurement_start_s, "end_s": measurement_end_s},
        "timestamps": ts, "environment": env,
    }

    (run_dir / "result.json").write_text(json.dumps(result, indent=2))
    (run_dir / "validity.json").write_text(json.dumps({"valid": valid, "checks": validity, "invalid_reasons": invalid_reasons}, indent=2))
    print(json.dumps({"valid": valid, "invalid_reasons": invalid_reasons, "color": color, "classification": classification}, indent=2))


if __name__ == "__main__":
    main()
