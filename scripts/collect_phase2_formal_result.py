#!/usr/bin/env python3
"""Collects one Phase 2 Unit 6 formal Benchmark measured-run result row.

See docs/test-plan/phase2-formal-protocol.md for the full methodology. Two
timestamp boundaries are used deliberately for two DIFFERENT purposes
(protocol section 4) — never conflate them:

  - [measurement_start, measurement_end]  (measurement_end = start + duration,
    computed, NOT observed after drain) -> THROUGHPUT population: completions
    that happened chronologically inside the nominal arrival window
    (`throughput_population` below — Prometheus-authoritative, see §6 in
    docs/decisions/phase2-formal-linux-environment.md for why this one is NOT
    affected by the warm-up-spillover issue discussed below).
  - [measurement_start, drain_end]  (drain_end observed after the measurement
    k6 process's own gracefulStop-driven drain) -> used ONLY for the
    `server_diagnostic` raw counts below, NOT for cohort accounting (see
    "Client cohort accounting" below for where the actual cohort — the
    population of requests that started during the arrival window and their
    terminal outcomes — comes from).

Client cohort accounting (2026-08-18 revision, docs/decisions/
phase2-formal-linux-environment.md §14): k6 is the SOLE source of truth for
"how many requests started during the measurement window and what happened
to them." k6's own measurement-run summary owns this population exactly —
its custom `measurement_iterations_started_total` Counter only increments for
measurement-phase iterations (03-constant-arrival-rate-continuous.js), and
its gracefulStop already waits for every one of them to reach a terminal
outcome before the process exits. The client-side invariant
`measurement_iterations_started_total == client_completed_total +
client_rejected_total + client_failed_mid_stream_total +
client_failed_no_event_total` is checked exactly (`client_cohort.invariant_ok`
below) — this holds by construction (the scenario's outcome branches are
mutually exclusive and exhaustive) and was empirically confirmed by the Unit
5.6 Mac functional smoke test.

Prometheus is NOT used to reconstruct or cross-check this cohort. An earlier
version of this script tried to do so — querying
`gateway_async_active_requests` at measurement_start to estimate how many
still-in-flight warm-up-arrived requests would leak into the
completion-time-windowed Prometheus outcome counters, and subtracting that
count as an "exact correction" before comparing to k6. That was reverted
(2026-08-18): (a) `gateway_async_active_requests` is a scraped gauge sample,
not the exact application state at the precise measurement_start instant —
under a 5s scrape interval there is really a possible discrepancy at the
boundary; (b) a warm-up-origin request still active at measurement_start is
not guaranteed to resolve as `completed` — it could just as validly resolve
as `timeout`/`upstream_error`/`client_disconnect`, and the subtraction
implicitly assumed "completed." Neither assumption is something this project
treats as good enough to call "exact." Prometheus's windowed outcome counters
over [measurement_start, drain_end] are kept as **diagnostic-only** raw
numbers (`server_diagnostic` below) — useful for spotting gross anomalies
(plateaus, unexpected_error spikes, timeout/failure bursts), not for
asserting a numeric match against k6's cohort. The two populations can
legitimately differ under the continuous lifecycle and are not forced to be
equal.

Usage:
  collect_phase2_formal_result.py <config_label> <thread_mode> <load_label> \
      <run_number> <measurement_start> <measurement_end> <drain_end> \
      <target_rate> <k6_summary_json_path> [prometheus_url]
"""
import json
import sys
import time as time_module
import urllib.parse
import urllib.request


def prom_query(base_url, expr, time=None):
    params = {"query": expr}
    if time is not None:
        params["time"] = str(time)
    url = f"{base_url}/api/v1/query?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        data = json.load(resp)
    result = data["data"]["result"]
    if not result:
        return 0.0
    return float(result[0]["value"][1])


def prom_query_range_max(base_url, expr, start, end, step=5):
    params = {"query": expr, "start": str(start), "end": str(end), "step": str(step)}
    url = f"{base_url}/api/v1/query_range?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        data = json.load(resp)
    result = data["data"]["result"]
    if not result:
        return 0.0
    values = [float(v[1]) for series in result for v in series["values"] if v[1] not in ("NaN",)]
    return max(values) if values else 0.0


def main():
    (config_label, thread_mode, load_label, run_number, meas_start, meas_end,
     drain_end, target_rate, k6_summary_path) = sys.argv[1:10]
    prom_url = sys.argv[10] if len(sys.argv) > 10 else "http://localhost:9091"
    meas_start, meas_end, drain_end = float(meas_start), float(meas_end), float(drain_end)
    target_rate = float(target_rate)
    window_s = meas_end - meas_start          # nominal arrival window (throughput)
    cohort_window_s = drain_end - meas_start  # superset window (outcome cohort)
    job = "gateway-mvc-java21"

    def q(expr, time=meas_end):
        return prom_query(prom_url, expr, time=time)

    def qr_max(expr, start=meas_start, end=meas_end):
        return prom_query_range_max(prom_url, expr, start, end)

    def windowed_count(metric_expr, start, end):
        # Exact integer delta between two instant-scraped values — see
        # Phase 1's collect_formal_result.py for why this is used instead of
        # increase() (which extrapolates/is fractional). Retries on an
        # impossible negative delta before giving up.
        for attempt in range(3):
            end_v = prom_query(prom_url, metric_expr, time=end)
            start_v = prom_query(prom_url, metric_expr, time=start)
            delta = end_v - start_v
            if delta >= 0:
                return delta
            print(f"WARNING: negative delta for {metric_expr!r} "
                  f"(start={start_v}, end={end_v}, attempt={attempt+1}/3), retrying",
                  file=sys.stderr)
            time_module.sleep(2)
        return delta

    # ---------------- A. Throughput population (nominal window only) ----------------
    completed_in_window = windowed_count(
        f'gateway_request_outcome_total{{job="{job}",outcome="completed"}}', meas_start, meas_end)
    measurement_window_completion_rate = completed_in_window / window_s if window_s > 0 else 0.0
    gateway_received_in_window = windowed_count(
        f'gateway_request_received_total{{job="{job}"}}', meas_start, meas_end)
    gateway_received_rate = gateway_received_in_window / window_s if window_s > 0 else None

    # ---------------- B. Server diagnostic (superset window, drain-inclusive; DIAGNOSTIC ONLY) ----------------
    # Raw Prometheus windowed counts over [measurement_start, drain_end].
    # `received_windowed` is arrival-time-windowed (gateway_request_received_total
    # increments at arrival) so it is NOT contaminated by warm-up spillover —
    # a warm-up arrival's timestamp is before meas_start, so it falls outside
    # this window already. The outcome counters below (completed/rejected/
    # timeout/failure) increment at COMPLETION time, so they CAN include
    # warm-up-arrived requests that happen to finish after meas_start under
    # the continuous lifecycle — see the module docstring for why this
    # project does not try to "correct" that. Use these numbers to eyeball
    # gross anomalies only, never as a cohort reconstruction.
    received_windowed = windowed_count(f'gateway_request_received_total{{job="{job}"}}', meas_start, drain_end)
    outcome_names = ["completed", "rejected", "timeout_before_start", "deadline_exceeded",
                      "upstream_timeout", "upstream_error", "client_disconnect", "unexpected_error"]
    outcomes = {o: windowed_count(f'gateway_request_outcome_total{{job="{job}",outcome="{o}"}}', meas_start, drain_end)
                for o in outcome_names}
    outcome_sum_windowed = sum(outcomes.values())
    completed_windowed = outcomes["completed"]
    rejected_windowed = outcomes["rejected"]
    timeout_windowed = outcomes["timeout_before_start"] + outcomes["deadline_exceeded"]
    failure_windowed = (outcomes["upstream_timeout"] + outcomes["upstream_error"]
                         + outcomes["client_disconnect"] + outcomes["unexpected_error"])
    unexpected_error_windowed = outcomes["unexpected_error"]

    # ---------------- k6 measurement-run summary: cohort source of truth (Unit 6 section 5) ----------------
    k6_iterations = None
    k6_completed = None
    k6_rejected = None
    k6_failed_mid = None
    k6_failed_no_event = None
    ttfc = {"p50": None, "p95": None, "p99": None}
    stream_duration = {"p50": None, "p95": None}
    dropped_iterations = None
    try:
        with open(k6_summary_path) as f:
            summary = json.load(f)
        metrics = summary.get("metrics", {})
        # k6's built-in `iterations` counts BOTH warm-up and measurement
        # iterations in the continuous single-process design
        # (03-constant-arrival-rate-continuous.js, docs/decisions/
        # phase2-formal-linux-environment.md item 2/3) and can't be filtered
        # after the fact — use the custom Counter that the scenario only
        # increments for measurement-phase iterations instead.
        k6_iterations = metrics.get("measurement_iterations_started_total", {}).get("count")
        k6_completed = metrics.get("client_completed_total", {}).get("count")
        k6_rejected = metrics.get("client_rejected_total", {}).get("count")
        k6_failed_mid = metrics.get("client_failed_mid_stream_total", {}).get("count")
        k6_failed_no_event = metrics.get("client_failed_no_event_total", {}).get("count")
        m = metrics.get("client_ttfc_completed_seconds", {})
        ttfc = {"p50": m.get("p(50)"), "p95": m.get("p(95)"), "p99": m.get("p(99)")}
        sd = metrics.get("client_stream_duration_seconds", {})
        stream_duration = {"p50": sd.get("p(50)"), "p95": sd.get("p(95)")}
        dropped_iterations = metrics.get("dropped_iterations", {}).get("count")
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    k6_failed_total = (k6_failed_mid or 0) + (k6_failed_no_event or 0)
    # Client cohort invariant: k6 is the sole authority for this population
    # (module docstring) — every measurement-phase iteration falls into
    # exactly one of these four mutually-exclusive, exhaustive outcome
    # branches (03-constant-arrival-rate-continuous.js), so this holds by
    # construction, not by cross-checking against a second, independent
    # measurement. Confirmed empirically by the Unit 5.6 Mac smoke test.
    client_cohort_invariant_ok = None
    if k6_iterations is not None:
        client_cohort_sum = (k6_completed or 0) + (k6_rejected or 0) + k6_failed_total
        client_cohort_invariant_ok = (k6_iterations == client_cohort_sum)

    actual_started_rate = (k6_iterations / window_s) if k6_iterations and window_s > 0 else None
    denom = (k6_iterations or 0) + (dropped_iterations or 0)
    dropped_iteration_rate = (dropped_iterations / denom) if dropped_iterations and denom > 0 else (0.0 if k6_iterations else None)

    def hist_q(metric, p, label_selector="", start=meas_start, end=meas_end):
        w = f"{int(end - start) + 2}s"
        expr = f'histogram_quantile({p}, sum(increase({metric}_bucket{{job="{job}"{label_selector}}}[{w}])) by (le))'
        return prom_query(prom_url, expr, time=end)

    gateway_ttfb = {p: hist_q("gateway_ttfb_seconds", f"0.{p}" if p != 99 else "0.99") for p in [50, 95, 99]}

    # ---------------- Resource metrics (nominal measurement window) ----------------
    platform_threads_window_peak = qr_max(f'jvm_threads_current{{job="{job}"}}')
    jvm_threads_peak_diagnostic = q(f'jvm_threads_peak{{job="{job}"}}')  # process-lifetime, NOT window-scoped

    rss_start = q(f'process_resident_memory_bytes{{job="{job}"}}', time=meas_start)
    rss_peak = qr_max(f'process_resident_memory_bytes{{job="{job}"}}')
    rss_delta = rss_peak - rss_start

    heap_start = q(f'jvm_memory_bytes_used{{job="{job}",area="heap"}}', time=meas_start)
    heap_peak = qr_max(f'jvm_memory_bytes_used{{job="{job}",area="heap"}}')
    heap_delta = heap_peak - heap_start

    cpu_avg = q(f'rate(process_cpu_seconds_total{{job="{job}"}}[{int(window_s)}s])')
    cpu_peak = qr_max(f'rate(process_cpu_seconds_total{{job="{job}"}}[30s])')

    gc_count_delta = windowed_count(f'jvm_gc_collection_seconds_count{{job="{job}"}}', meas_start, meas_end)
    gc_pause_sum_delta = windowed_count(f'jvm_gc_collection_seconds_sum{{job="{job}"}}', meas_start, meas_end)

    mock_current_concurrency_peak = qr_max("mockllm_current_concurrency")
    mock_waiting_peak = qr_max("mockllm_waiting_requests")
    mock_bottleneck = mock_waiting_peak > 0

    row = {
        "configuration": config_label,
        "thread_mode": thread_mode,
        "load": load_label,
        "run_number": int(float(run_number)),
        "arrival_rate_target": target_rate,
        "actual_started_rate": actual_started_rate,
        "gateway_received_rate": gateway_received_rate,
        "measurement_window_completion_rate": measurement_window_completion_rate,
        "dropped_iteration_rate": dropped_iteration_rate,
        "dropped_iterations": dropped_iterations,
        "measurement_window_s": window_s,
        "cohort_window_s": cohort_window_s,

        "throughput_population": {
            "note": "completions that happened chronologically inside [measurement_start, measurement_end] only",
            "completed_in_window": completed_in_window,
            "gateway_received_in_window": gateway_received_in_window,
        },
        "client_cohort": {
            "note": "k6-authoritative: requests that STARTED in the measurement phase (03-constant-arrival-rate-continuous.js phase gating), tracked to terminal outcome through k6's own gracefulStop drain. Sole source of truth for Formal request-outcome cohort accounting — see module docstring for why Prometheus aggregate counters are not used to reconstruct this population.",
            "measurement_iterations_started_total": k6_iterations,
            "completed": k6_completed,
            "rejected": k6_rejected,
            "failed_mid_stream": k6_failed_mid,
            "failed_no_event": k6_failed_no_event,
            "failed_total": k6_failed_total,
            "invariant_ok": client_cohort_invariant_ok,
        },
        "server_diagnostic": {
            "note": "Prometheus raw windowed counts over [measurement_start, drain_end] (outcome counters are completion-time-windowed). DIAGNOSTIC ONLY — under the continuous warm-up->measurement lifecycle these are NOT expected to numerically match client_cohort (a warm-up-arrived request still in flight at measurement_start can resolve after it, inflating these raw counts by an amount that depends on its eventual outcome, which is not knowable from this gauge alone). Use only to spot gross anomalies (plateaus, unexpected_error spikes, timeout/failure bursts) — see also stall_check.json for the log-gap/counter-plateau combined judgment.",
            "diagnostic_only": True,
            "received_windowed": received_windowed,
            "completed_windowed": completed_windowed,
            "rejected_windowed": rejected_windowed,
            "timeout_windowed": timeout_windowed,
            "failure_windowed": failure_windowed,
            "unexpected_error_windowed": unexpected_error_windowed,
            "outcome_sum_windowed": outcome_sum_windowed,
        },

        "ttfc_completed_p50": ttfc["p50"],
        "ttfc_completed_p95": ttfc["p95"],
        "ttfc_completed_p99": ttfc["p99"],
        "client_stream_duration_p50": stream_duration["p50"],
        "client_stream_duration_p95": stream_duration["p95"],
        "gateway_ttfb_p50": gateway_ttfb[50],
        "gateway_ttfb_p95": gateway_ttfb[95],
        "gateway_ttfb_p99": gateway_ttfb[99],

        "platform_threads_window_peak": platform_threads_window_peak,
        "jvm_threads_peak_diagnostic_process_lifetime": jvm_threads_peak_diagnostic,

        "rss_measurement_start_bytes": rss_start,
        "rss_peak_bytes": rss_peak,
        "rss_delta_bytes": rss_delta,
        "heap_measurement_start_bytes": heap_start,
        "heap_peak_bytes": heap_peak,
        "heap_delta_bytes": heap_delta,
        "cpu_avg_cores": cpu_avg,
        "cpu_peak_cores": cpu_peak,
        "gc_count_in_window": gc_count_delta,
        "gc_pause_seconds_in_window": gc_pause_sum_delta,

        "mock_current_concurrency_peak": mock_current_concurrency_peak,
        "mock_waiting_peak": mock_waiting_peak,
        "mock_bottleneck": mock_bottleneck,
    }
    print(json.dumps(row, indent=2))


if __name__ == "__main__":
    main()
