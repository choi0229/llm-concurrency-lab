#!/usr/bin/env python3
"""Collects one Unit 6 formal Benchmark measured-run result row, restricted to
the [measurement_start, measurement_end] window ONLY (warm-up excluded) —
see docs/test-results/phase1/unit6-benchmark-plan.md section 5.

Unlike Unit 5's collect_screening_result.py (which relied on a fresh
container per data point, so instant/whole-lifetime queries were exact), a
formal run's containers stay up across warm-up + measurement, so every query
here is a windowed increase()/rate()/query_range over the measurement window
specifically.

Usage:
  collect_formal_result.py <config_label> <load_label> <run_number> \
      <measurement_start> <measurement_end> <target_rate> <k6_summary_json_path> \
      [prometheus_url]
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
    (config_label, load_label, run_number, meas_start, meas_end, target_rate,
     k6_summary_path) = sys.argv[1:8]
    prom_url = sys.argv[8] if len(sys.argv) > 8 else "http://localhost:9091"
    meas_start, meas_end = float(meas_start), float(meas_end)
    target_rate = float(target_rate)
    window_s = (meas_end - meas_start)
    window = f"{int(window_s) + 2}s"

    def q(expr):
        return prom_query(prom_url, expr, time=meas_end)

    def qr_max(expr):
        return prom_query_range_max(prom_url, expr, meas_start, meas_end)

    def windowed_count(metric_expr):
        # Exact integer delta between two instant-scraped values, NOT
        # increase() — increase() extrapolates to the window boundary and
        # returns a fractional estimate, which fails the exact accounting
        # invariant (docs/decisions/request-outcome-accounting.md) by
        # design here we want the literal counter delta.
        #
        # A negative delta is impossible for a monotonic Counter within one
        # run's lifetime (no restart in between) — 3 of Unit 6's first 21
        # runs hit exactly this (e.g. -4465 for a metric that should have
        # been ~0 all run), never conclusively root-caused (a transient
        # Prometheus HTTP query issue, not reproduced under retry in
        # isolation). Retry a few times before giving up rather than
        # silently emitting impossible data — see
        # docs/test-results/phase1/unit6-benchmark-plan.md.
        for attempt in range(3):
            end_v = prom_query(prom_url, metric_expr, time=meas_end)
            start_v = prom_query(prom_url, metric_expr, time=meas_start)
            delta = end_v - start_v
            if delta >= 0:
                return delta
            print(f"WARNING: negative delta for {metric_expr!r} "
                  f"(start={start_v}, end={end_v}, attempt={attempt+1}/3), retrying",
                  file=sys.stderr)
            time_module.sleep(2)
        return delta

    received = windowed_count("gateway_request_received_total")
    outcomes = {}
    for outcome in ["completed", "rejected", "timeout_before_start", "deadline_exceeded",
                     "upstream_timeout", "upstream_error", "client_disconnect", "unexpected_error"]:
        outcomes[outcome] = windowed_count(f'gateway_request_outcome_total{{outcome="{outcome}"}}')
    outcome_sum = sum(outcomes.values())
    invariant_ok = abs(received - outcome_sum) < 0.5 and outcomes["unexpected_error"] < 0.5

    caller_runs = windowed_count('executor_task_execution_total{mode="caller"}')

    completed = outcomes["completed"]
    rejected = outcomes["rejected"]
    timeout_n = outcomes["timeout_before_start"] + outcomes["deadline_exceeded"]
    failure_n = (outcomes["upstream_timeout"] + outcomes["upstream_error"]
                 + outcomes["client_disconnect"] + outcomes["unexpected_error"])

    completion_rate = completed / window_s if window_s > 0 else 0.0
    completion_ratio = completed / received if received > 0 else None
    reject_rate = rejected / received if received > 0 else None
    timeout_rate = timeout_n / received if received > 0 else None
    failure_rate = failure_n / received if received > 0 else None

    def hist_q(metric, p, label_selector=""):
        expr = f'histogram_quantile({p}, sum(increase({metric}_bucket{label_selector}[{window}])) by (le))'
        return q(expr)

    ttfb = {p: hist_q("gateway_ttfb_seconds", f"0.{p}" if p != 99 else "0.99") for p in [50, 95, 99]}
    qw_completed = {p: hist_q("executor_queue_wait_seconds", f"0.{p}" if p != 99 else "0.99",
                               '{outcome="completed"}') for p in [50, 95, 99]}
    qw_failed = {p: hist_q("executor_queue_wait_seconds", f"0.{p}" if p != 99 else "0.99",
                            '{outcome="failed"}') for p in [50, 95, 99]}

    pool_size_peak = qr_max("gateway_executor_pool_size")
    active_thread_peak = qr_max("gateway_executor_active_threads")
    queue_size_peak = qr_max("gateway_executor_queue_size")
    async_active_peak = qr_max("gateway_async_active_requests")
    platform_thread_peak = qr_max("jvm_threads_current")

    cpu_avg = q(f'rate(process_cpu_seconds_total{{job="gateway-mvc-executor-java8"}}[{window}])')
    cpu_peak = prom_query_range_max(
        prom_url, 'rate(process_cpu_seconds_total{job="gateway-mvc-executor-java8"}[30s])',
        meas_start, meas_end)
    heap_peak = qr_max('jvm_memory_bytes_used{area="heap"}')
    rss_peak = qr_max("process_resident_memory_bytes")

    mock_active_peak = qr_max("mockllm_active_requests")
    mock_waiting_peak = qr_max("mockllm_waiting_requests")
    mock_bottleneck = mock_waiting_peak > 0

    ttfc = {"p50": None, "p95": None, "p99": None}
    dropped_iterations = None
    achieved_iterations = None
    try:
        with open(k6_summary_path) as f:
            summary = json.load(f)
        m = summary.get("metrics", {}).get("client_ttfc_completed_seconds", {})
        ttfc = {"p50": m.get("p(50)"), "p95": m.get("p(95)"), "p99": m.get("p(99)")}
        dropped_iterations = summary.get("metrics", {}).get("dropped_iterations", {}).get("count")
        achieved_iterations = summary.get("metrics", {}).get("iterations", {}).get("count")
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    # Explicit target vs. actually-delivered rate accounting (never assert
    # "no real impact" from a nonzero dropped_iterations without these —
    # see docs/test-results/phase1/unit6-formal-benchmark-results.md).
    denom = (achieved_iterations or 0) + (dropped_iterations or 0)
    actual_started_rate = (achieved_iterations / window_s) if achieved_iterations and window_s > 0 else None
    gateway_received_rate = (received / window_s) if window_s > 0 else None
    dropped_iteration_rate = (dropped_iterations / denom) if dropped_iterations and denom > 0 else (0.0 if achieved_iterations else None)

    row = {
        "configuration": config_label,
        "load": load_label,
        "run_number": int(float(run_number)),
        "arrival_rate_target": target_rate,
        "actual_started_rate": actual_started_rate,
        "gateway_received_rate": gateway_received_rate,
        "dropped_iteration_rate": dropped_iteration_rate,
        "measurement_window_s": window_s,
        "received": received,
        "completed": completed,
        "rejected": rejected,
        "timeout": timeout_n,
        "failed": failure_n,
        "caller_runs": caller_runs,
        "completion_rate_per_s": completion_rate,
        "completion_ratio": completion_ratio,
        "reject_rate": reject_rate,
        "timeout_rate": timeout_rate,
        "failure_rate": failure_rate,
        "ttfc_completed_p50": ttfc["p50"],
        "ttfc_completed_p95": ttfc["p95"],
        "ttfc_completed_p99": ttfc["p99"],
        "gateway_ttfb_p50": ttfb[50],
        "gateway_ttfb_p95": ttfb[95],
        "gateway_ttfb_p99": ttfb[99],
        "queue_wait_completed_p50": qw_completed[50],
        "queue_wait_completed_p95": qw_completed[95],
        "queue_wait_completed_p99": qw_completed[99],
        "queue_wait_failed_p50": qw_failed[50],
        "queue_wait_failed_p95": qw_failed[95],
        "queue_wait_failed_p99": qw_failed[99],
        "pool_size_peak": pool_size_peak,
        "active_thread_peak": active_thread_peak,
        "queue_size_peak": queue_size_peak,
        "async_active_peak": async_active_peak,
        "platform_thread_peak": platform_thread_peak,
        "cpu_avg_cores": cpu_avg,
        "cpu_peak_cores": cpu_peak,
        "heap_peak_bytes": heap_peak,
        "rss_peak_bytes": rss_peak,
        "mock_active_peak": mock_active_peak,
        "mock_waiting_peak": mock_waiting_peak,
        "mock_bottleneck": mock_bottleneck,
        "dropped_iterations": dropped_iterations,
        "achieved_iterations": achieved_iterations,
        "invariant_ok": invariant_ok,
        "outcome_sum": outcome_sum,
    }
    print(json.dumps(row, indent=2))


if __name__ == "__main__":
    main()
