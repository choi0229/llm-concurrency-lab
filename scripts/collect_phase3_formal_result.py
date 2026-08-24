#!/usr/bin/env python3
"""Phase 3 Unit 6.5 Formal result collector (docs/test-plan/phase3-formal-protocol.md Sec 25).

Reads one run's raw artifacts (timestamps.json, environment.json, k6-summary.json,
prometheus-samples.csv, rss-samples.csv, prometheus-measurement-start.txt,
prometheus-measurement-end.txt, prometheus-postflight.txt) and emits result.json per the frozen
schema. Never fabricates a 0 for an implementation-specific metric that structurally does not
exist for a given config (Sec 21/24) -- such fields are null.

Usage: collect_phase3_formal_result.py <OUT_DIR> <CFG:A|B|C> <INVALID_REASON> <POSTFLIGHT_FAIL>
"""
import csv
import json
import re
import sys


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def read_text(path):
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        return ""


def prom_metric(text, name):
    """Returns the value of a Prometheus text-format metric with no labels, or None."""
    m = re.search(rf'^{re.escape(name)} ([0-9eE.+\-]+)$', text, re.MULTILINE)
    return float(m.group(1)) if m else None


def prom_metric_labeled(text, name):
    """Returns the (first) value of a Prometheus text-format metric that DOES have labels."""
    m = re.search(rf'^{re.escape(name)}\{{[^}}]*\}} ([0-9eE.+\-]+)$', text, re.MULTILINE)
    return float(m.group(1)) if m else None


def prom_any(text, name):
    v = prom_metric(text, name)
    return v if v is not None else prom_metric_labeled(text, name)


def prom_exact(text, full_series):
    """Matches one exact, fully-specified series (name + its literal label string, e.g.
    'gateway_requests_total{outcome="completed",}') -- unlike prom_metric_labeled, which expects
    a bare name and matches ANY label set, this requires the caller's label string verbatim."""
    m = re.search(rf'^{re.escape(full_series)} ([0-9eE.+\-]+)$', text, re.MULTILINE)
    return float(m.group(1)) if m else None


def prom_sum_labeled(text, name):
    """Sums every labeled series for a metric broken down by label (e.g. jvm_memory_used_bytes's
    area/pool labels, jvm_gc_pause_seconds_*'s cause/action labels) -- a single first-match grab
    would silently pick one arbitrary series instead of the total."""
    total = 0.0
    found = False
    for m in re.finditer(rf'^{re.escape(name)}\{{[^}}]*\}} ([0-9eE.+\-]+)$', text, re.MULTILINE):
        total += float(m.group(1))
        found = True
    return total if found else None


def read_csv_rows(path):
    rows = []
    try:
        with open(path) as f:
            r = csv.DictReader(f)
            for row in r:
                rows.append(row)
    except FileNotFoundError:
        pass
    return rows


def fnum(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except ValueError:
        return None


def window_stats(rows, ts_key, value_key, start, end):
    vals = []
    for row in rows:
        ts = fnum(row.get(ts_key))
        v = fnum(row.get(value_key))
        if ts is None or v is None:
            continue
        if start <= ts <= end:
            vals.append((ts, v))
    if not vals:
        return {"start": None, "peak": None, "delta": None, "mean": None}
    vals.sort(key=lambda x: x[0])
    first_v = vals[0][1]
    peak_v = max(v for _, v in vals)
    mean_v = sum(v for _, v in vals) / len(vals)
    return {"start": first_v, "peak": peak_v, "delta": peak_v - first_v, "mean": mean_v}


def k6_metric_value(k6, name, default=0.0):
    m = k6.get("metrics", {}).get(name, {})
    return m.get("value", m.get("count", default))


def k6_trend(k6, name):
    m = k6.get("metrics", {}).get(name, {})
    return {
        "p50": m.get("p(50)"), "p95": m.get("p(95)"), "p99": m.get("p(99)"),
        "min": m.get("min"), "max": m.get("max"), "avg": m.get("avg"),
    }


def main():
    out_dir, cfg, invalid_reason_in, postflight_fail = sys.argv[1:5]

    timestamps = read_json(f"{out_dir}/timestamps.json")
    environment = read_json(f"{out_dir}/environment.json")
    k6 = read_json(f"{out_dir}/k6-summary.json")

    meas_start = timestamps.get("measurement_start", 0)
    meas_end = timestamps.get("measurement_end", 0)
    window_s = (meas_end - meas_start) if (meas_start and meas_end) else 0

    snap_start_text = read_text(f"{out_dir}/prometheus-measurement-start.txt")
    snap_end_text = read_text(f"{out_dir}/prometheus-measurement-end.txt")
    postflight_text = read_text(f"{out_dir}/prometheus-postflight.txt")

    # ---------------- Client cohort (Sec 12-B, k6-authoritative) ----------------
    started = k6_metric_value(k6, "measurement_iterations_started_total")
    completed = k6_metric_value(k6, "client_completed_total")
    rejected = k6_metric_value(k6, "client_rejected_total")
    failed_mid = k6_metric_value(k6, "client_failed_mid_stream_total")
    failed_no_event = k6_metric_value(k6, "client_failed_no_event_total")
    invariant_ok = abs(started - (completed + rejected + failed_mid + failed_no_event)) < 0.5
    dropped_iterations = k6_metric_value(k6, "dropped_iterations", 0.0)

    client_cohort = {
        "measurement_iterations_started_total": started,
        "client_completed_total": completed,
        "client_rejected_total": rejected,
        "client_failed_mid_stream_total": failed_mid,
        "client_failed_no_event_total": failed_no_event,
        "invariant_ok": invariant_ok,
        "dropped_iterations": dropped_iterations,
    }

    # ---------------- Throughput (Sec 17: population A, chronological-window) ----------------
    completed_start = prom_exact(snap_start_text, 'gateway_requests_total{outcome="completed",}')
    completed_end = prom_exact(snap_end_text, 'gateway_requests_total{outcome="completed",}')
    completion_throughput = None
    if completed_start is not None and completed_end is not None and window_s > 0:
        completion_throughput = (completed_end - completed_start) / window_s

    throughput = {
        "target_arrival_rate": environment.get("rate"),
        "completion_throughput_chronological": completion_throughput,
        "client_completed_rate_cohort": (completed / window_s) if window_s > 0 else None,
        "note": "chronological = population A (Sec 17, gateway_requests_total{outcome=completed} "
                "delta between the measurement-start/measurement-end Prometheus snapshots, divided "
                "by window_s); cohort rate = population B (client-arrived-in-window cohort's own "
                "completed count / window_s) -- both reported, not conflated (protocol Sec 12).",
    }

    # ---------------- Client latency (Sec 18/19: completed only, k6 client-side) ----------------
    client_latency = {
        "ttfc_completed_seconds": k6_trend(k6, "client_ttfc_completed_seconds"),
        "stream_duration_completed_seconds": k6_trend(k6, "client_stream_duration_seconds"),
    }

    # ---------------- Platform threads (Sec 20: window peak only) ----------------
    prom_rows = read_csv_rows(f"{out_dir}/prometheus-samples.csv")
    threads_w = window_stats(prom_rows, "ts", "jvm_threads_live", meas_start, meas_end)
    platform_threads = {"window_peak": threads_w["peak"]}

    # ---------------- CPU (Sec 21, corrected by the Unit 7 Canary finding) ----------------
    # `process_cpu_seconds_total` (a cumulative counter) turned out NOT to be exposed by this
    # stack's actual /actuator/prometheus output (Boot 2.7.18 / Micrometer 1.9.17) -- only
    # `process_cpu_usage`, an instantaneous GAUGE in [0.0, 1.0] (Micrometer ProcessorMetrics /
    # OperatingSystemMXBean#getProcessCpuLoad() semantics: 1.0 == the process was using ALL
    # available processors 100% of the time). The originally-frozen "cumulative delta / window_s"
    # formula silently produced null on real data -- caught by the Canary, not assumed. Corrected
    # formula: mean(process_cpu_usage samples in window) * available_processors -- converts the
    # "fraction of total capacity" gauge into "average cores consumed", the same unit choice
    # protocol Sec 21 always intended, just computed from the metric that actually exists.
    cpu_usage_w = window_stats(prom_rows, "ts", "process_cpu_usage", meas_start, meas_end)
    available_processors = environment.get("available_processors")
    cpu_avg_cores = None
    if cpu_usage_w["mean"] is not None and available_processors:
        cpu_avg_cores = cpu_usage_w["mean"] * available_processors
    cpu = {
        "avg_cores": cpu_avg_cores,
        "peak_instantaneous_usage_fraction": cpu_usage_w["peak"],
        "mean_usage_fraction": cpu_usage_w["mean"],
        "note": "avg_cores = mean(process_cpu_usage in window) * available_processors -- "
                "process_cpu_seconds_total is not exposed by this stack (Unit 7 Canary finding, "
                "docs/test-plan/phase3-formal-protocol.md Sec 21).",
    }

    # ---------------- Native RSS (Sec 22: start/peak/delta, all preserved) ----------------
    rss_rows = read_csv_rows(f"{out_dir}/rss-samples.csv")
    rss_w = window_stats(rss_rows, "ts", "rss_kib", meas_start, meas_end)
    native_rss = {
        "measurement_start_bytes": (rss_w["start"] * 1024) if rss_w["start"] is not None else None,
        "measurement_peak_bytes": (rss_w["peak"] * 1024) if rss_w["peak"] is not None else None,
        "measurement_delta_bytes": (rss_w["delta"] * 1024) if rss_w["delta"] is not None else None,
    }

    # ---------------- Heap / GC (Sec 23: secondary) ----------------
    heap_w = window_stats(prom_rows, "ts", "heap_used", meas_start, meas_end)
    gc_count_start = prom_sum_labeled(snap_start_text, "jvm_gc_pause_seconds_count")
    gc_count_end = prom_sum_labeled(snap_end_text, "jvm_gc_pause_seconds_count")
    gc_sum_start = prom_sum_labeled(snap_start_text, "jvm_gc_pause_seconds_sum")
    gc_sum_end = prom_sum_labeled(snap_end_text, "jvm_gc_pause_seconds_sum")
    heap_gc = {
        "heap_start_bytes": heap_w["start"], "heap_peak_bytes": heap_w["peak"], "heap_delta_bytes": heap_w["delta"],
        "gc_pause_count_delta": (gc_count_end - gc_count_start) if (gc_count_start is not None and gc_count_end is not None) else None,
        "gc_pause_seconds_delta": (gc_sum_end - gc_sum_start) if (gc_sum_start is not None and gc_sum_end is not None) else None,
    }

    # ---------------- Admission (Sec 9/10: diagnostic window shape, not a single scalar claim) --
    adm_w = window_stats(prom_rows, "ts", "admission_active", meas_start, meas_end)
    streams_w = window_stats(prom_rows, "ts", "active_streams", meas_start, meas_end)
    admission = {
        "admission_active_window_peak": adm_w["peak"], "admission_active_window_mean": adm_w["mean"],
        "active_streams_window_peak_diagnostic_only": streams_w["peak"],
        "note": "gateway_active_streams is implementation-local diagnostic only (Sec 10) -- never "
                "used as a cross-implementation capacity claim.",
    }

    # ---------------- Implementation-specific (Sec 24) ----------------
    impl = {}
    if cfg == "A":
        be_w = window_stats(prom_rows, "ts", "blocking_executor_active", meas_start, meas_end)
        be_rej_start = prom_any(snap_start_text, "outbound_blocking_executor_rejected_total")
        be_rej_end = prom_any(snap_end_text, "outbound_blocking_executor_rejected_total")
        impl["p3a_blocking_outbound_executor"] = {
            "active_window_peak": be_w["peak"],
            "rejected_delta": (be_rej_end - be_rej_start) if (be_rej_start is not None and be_rej_end is not None) else None,
        }
    if cfg in ("A", "B"):
        sw_active_w = window_stats(prom_rows, "ts", "servlet_write_active", meas_start, meas_end)
        sw_queue_w = window_stats(prom_rows, "ts", "servlet_write_queue", meas_start, meas_end)
        sw_buf_w = window_stats(prom_rows, "ts", "servlet_write_buffered", meas_start, meas_end)
        overflow_start = prom_any(snap_start_text, "gateway_write_overflow_total")
        overflow_end = prom_any(snap_end_text, "gateway_write_overflow_total")
        impl["servlet_write_path"] = {
            "active_window_peak": sw_active_w["peak"], "queue_depth_window_peak": sw_queue_w["peak"],
            "buffered_frames_window_peak": sw_buf_w["peak"],
            "write_overflow_delta": (overflow_end - overflow_start) if (overflow_start is not None and overflow_end is not None) else None,
        }
    else:
        impl["servlet_write_path"] = None
    if cfg in ("B", "C"):
        pending_w = window_stats(prom_rows, "ts", "conn_pending", meas_start, meas_end)
        impl["reactor_netty_connection_pool"] = {"pending_connections_window_peak": pending_w["peak"]}
    else:
        impl["reactor_netty_connection_pool"] = None

    # ---------------- Postflight ----------------
    postflight = {
        "gateway_admission_active": prom_any(postflight_text, "gateway_admission_active"),
        "gateway_active_streams": prom_any(postflight_text, "gateway_active_streams"),
        "gateway_upstream_active": prom_any(postflight_text, "gateway_upstream_active"),
        "fail_summary": postflight_fail if postflight_fail else None,
    }

    # ---------------- Validity determination ----------------
    reasons = []
    if invalid_reason_in:
        reasons.append(invalid_reason_in)
    if not invariant_ok:
        reasons.append("client_cohort_invariant_failed")
    if dropped_iterations and dropped_iterations > 0:
        reasons.append(f"dropped_iterations={dropped_iterations}")
    valid = len(reasons) == 0

    result = {
        "valid": valid,
        "invalid_reason": ",".join(reasons) if reasons else None,
        "client_cohort": client_cohort,
        "throughput": throughput,
        "client_latency": client_latency,
        "platform_threads": platform_threads,
        "cpu": cpu,
        "native_rss": native_rss,
        "heap_gc": heap_gc,
        "admission": admission,
        "implementation_specific": impl,
        "postflight": postflight,
        "environment": environment,
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
