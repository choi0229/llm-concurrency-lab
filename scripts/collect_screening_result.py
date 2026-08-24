#!/usr/bin/env python3
"""Collects one Unit 5 Executor Matrix Screening run's result row.

Run AFTER the gateway has fully drained (gateway_async_active_requests == 0)
for the [start_ts, end_ts] window of a single k6 run against a freshly
(re)started gateway + mock-llm pair — see scripts/run-screening.sh, which
calls this. A fresh container per run means every Prometheus counter queried
here already starts at 0 for this run, so instant queries at end_ts with a
range covering the whole run are equivalent to windowed queries.

Usage:
  collect_screening_result.py <config_label> <concurrency> <start_ts> <end_ts> \
      <k6_summary_json_path> [prometheus_url]
"""
import json
import sys
import urllib.parse
import urllib.request


def prom_query(base_url, expr, time=None):
    params = {"query": expr}
    if time is not None:
        params["time"] = str(time)
    url = f"{base_url}/api/v1/query?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        data = json.load(resp)
    result = data["data"]["result"]
    if not result:
        return 0.0
    return float(result[0]["value"][1])


def prom_query_range_max(base_url, expr, start, end, step=5):
    params = {"query": expr, "start": str(start), "end": str(end), "step": str(step)}
    url = f"{base_url}/api/v1/query_range?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        data = json.load(resp)
    result = data["data"]["result"]
    if not result:
        return 0.0
    values = [float(v[1]) for series in result for v in series["values"]]
    return max(values) if values else 0.0


def main():
    config_label, concurrency, start_ts, end_ts, k6_summary_path = sys.argv[1:6]
    prom_url = sys.argv[6] if len(sys.argv) > 6 else "http://localhost:9091"
    start_ts, end_ts = float(start_ts), float(end_ts)
    window = f"{int(end_ts - start_ts) + 5}s"

    def q(expr):
        return prom_query(prom_url, expr, time=end_ts)

    def qr_max(expr):
        return prom_query_range_max(prom_url, expr, start_ts, end_ts)

    received = q("gateway_request_received_total")
    outcomes = {}
    for outcome in ["completed", "rejected", "timeout_before_start", "deadline_exceeded",
                     "upstream_timeout", "upstream_error", "client_disconnect", "unexpected_error"]:
        outcomes[outcome] = q(f'gateway_request_outcome_total{{outcome="{outcome}"}}')
    outcome_sum = sum(outcomes.values())
    stale = q("gateway_stale_task_skipped_total")
    caller_runs = q('executor_task_execution_total{mode="caller"}')

    accepted = received - outcomes["rejected"]
    timeout_total = outcomes["timeout_before_start"] + outcomes["deadline_exceeded"]
    failed_total = (outcomes["upstream_timeout"] + outcomes["upstream_error"]
                     + outcomes["client_disconnect"] + outcomes["unexpected_error"])

    invariant_ok = abs(received - outcome_sum) < 0.5

    pool_size_peak = q("gateway_executor_largest_pool_size")
    active_threads_peak = qr_max("gateway_executor_active_threads")
    queue_size_peak = qr_max("gateway_executor_queue_size")
    async_active_peak = qr_max("gateway_async_active_requests")

    def hist_q(metric, p, label_selector=""):
        expr = f'histogram_quantile({p}, sum(increase({metric}_bucket{label_selector}[{window}])) by (le))'
        v = q(expr)
        return v

    ttfc = {}
    try:
        with open(k6_summary_path) as f:
            summary = json.load(f)
        # k6's --summary-export nests metrics under "metrics" (sibling:
        # "root_group"), with whatever --summary-trend-stats requested
        # (run-screening.sh passes "p(50),p(95),p(99),min,max") as direct
        # keys — no further "values" nesting.
        m = summary.get("metrics", {}).get("client_ttfc_seconds", {})
        ttfc = {"p50": m.get("p(50)"), "p95": m.get("p(95)"), "p99": m.get("p(99)")}
    except (FileNotFoundError, json.JSONDecodeError):
        ttfc = {"p50": None, "p95": None, "p99": None}

    qw_completed = {p: hist_q("executor_queue_wait_seconds", f"0.{p}" if p != 99 else "0.99",
                               '{outcome="completed"}') for p in [50, 95, 99]}
    qw_failed = {p: hist_q("executor_queue_wait_seconds", f"0.{p}" if p != 99 else "0.99",
                            '{outcome="failed"}') for p in [50, 95, 99]}
    ttfb_p95 = hist_q("gateway_ttfb_seconds", "0.95")

    cpu_gateway = q(f'rate(process_cpu_seconds_total{{job="gateway-mvc-executor-java8"}}[{window}])')
    heap_peak = qr_max('jvm_memory_bytes_used{area="heap"}')
    rss_peak = qr_max("process_resident_memory_bytes")
    platform_thread_peak = qr_max("jvm_threads_current")

    mock_waiting_peak = qr_max("mockllm_waiting_requests")
    mock_bottleneck = mock_waiting_peak > 0

    row = {
        "configuration": config_label,
        "concurrency": int(float(concurrency)),
        "accepted": accepted,
        "completed": outcomes["completed"],
        "failed": failed_total,
        "rejected": outcomes["rejected"],
        "timeout": timeout_total,
        "stale": stale,
        "caller_runs": caller_runs,
        "pool_size_peak": pool_size_peak,
        "active_threads_peak": active_threads_peak,
        "queue_size_peak": queue_size_peak,
        "async_active_peak": async_active_peak,
        "ttfc_p50": ttfc["p50"],
        "ttfc_p95": ttfc["p95"],
        "ttfc_p99": ttfc["p99"],
        "queue_wait_completed_p50": qw_completed[50],
        "queue_wait_completed_p95": qw_completed[95],
        "queue_wait_completed_p99": qw_completed[99],
        "queue_wait_failed_p50": qw_failed[50],
        "queue_wait_failed_p95": qw_failed[95],
        "queue_wait_failed_p99": qw_failed[99],
        "gateway_ttfb_p95": ttfb_p95,
        "cpu_cores_avg": cpu_gateway,
        "heap_peak_bytes": heap_peak,
        "rss_peak_bytes": rss_peak,
        "platform_thread_peak": platform_thread_peak,
        "mock_waiting_peak": mock_waiting_peak,
        "mock_bottleneck": mock_bottleneck,
        "invariant_ok": invariant_ok,
        "received": received,
        "outcome_sum": outcome_sum,
    }
    print(json.dumps(row))


if __name__ == "__main__":
    main()
