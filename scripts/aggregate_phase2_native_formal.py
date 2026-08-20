#!/usr/bin/env python3
"""Phase 2 Unit 6 Formal Benchmark — Native macOS ARM64 aggregation.

Reads the 18 valid runs under docs/test-results/phase2/unit6-native/
({pe,vtl}-{r3,r6,r8}-run{1,2,3}) and computes median/min/max/CV per
(config, load) cell for the metrics the ADR/protocol treat as authoritative.

RSS: uses native_resource.rss_measurement_{start,peak,delta}_bytes only —
the top-level rss_measurement_*_bytes fields are always 0 on native macOS
(process_resident_memory_bytes isn't exposed there) and must never be used,
per docs/decisions/phase2-formal-native-macos-environment.md.

Built only after all 18 valid runs were confirmed, per the project's
established practice of not aggregating/interpreting partial results.
"""
import json
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(ROOT, "docs/test-results/phase2/unit6-native")

CONFIGS = ["pe", "vtl"]
LOADS = ["r3", "r6", "r8"]
REPS = [1, 2, 3]


def load_run(config, load, rep):
    d = os.path.join(RESULTS_DIR, f"{config}-{load}-run{rep}")
    with open(os.path.join(d, "result.json")) as f:
        result = json.load(f)
    with open(os.path.join(d, "environment.json")) as f:
        env = json.load(f)
    return result, env


def stats(values):
    values = [v for v in values if v is not None]
    if not values:
        return {"median": None, "min": None, "max": None, "cv_pct": None, "n": 0}
    med = statistics.median(values)
    lo, hi = min(values), max(values)
    if len(values) > 1 and statistics.mean(values) not in (0, None):
        mean = statistics.mean(values)
        sd = statistics.stdev(values)
        cv = (sd / mean * 100.0) if mean else None
    else:
        cv = None
    return {"median": med, "min": lo, "max": hi, "cv_pct": cv, "n": len(values)}


METRICS = {
    "measurement_window_completion_rate": lambda r, e: r.get("measurement_window_completion_rate"),
    "actual_started_rate": lambda r, e: r.get("actual_started_rate"),
    "gateway_received_rate": lambda r, e: r.get("gateway_received_rate"),
    "client_completed": lambda r, e: r.get("client_cohort", {}).get("completed"),
    "client_rejected": lambda r, e: r.get("client_cohort", {}).get("rejected") or 0,
    "measurement_iterations_started_total": lambda r, e: r.get("client_cohort", {}).get("measurement_iterations_started_total"),
    "rejection_ratio": lambda r, e: (
        (r.get("client_cohort", {}).get("rejected") or 0)
        / r.get("client_cohort", {}).get("measurement_iterations_started_total")
        if r.get("client_cohort", {}).get("measurement_iterations_started_total") else None
    ),
    "ttfc_p50": lambda r, e: r.get("ttfc_completed_p50"),
    "ttfc_p95": lambda r, e: r.get("ttfc_completed_p95"),
    "ttfc_p99": lambda r, e: r.get("ttfc_completed_p99"),
    "stream_duration_p50": lambda r, e: r.get("client_stream_duration_p50"),
    "stream_duration_p95": lambda r, e: r.get("client_stream_duration_p95"),
    "platform_threads_window_peak": lambda r, e: r.get("platform_threads_window_peak"),
    "cpu_avg_cores": lambda r, e: r.get("cpu_avg_cores"),
    "cpu_peak_cores": lambda r, e: r.get("cpu_peak_cores"),
    "heap_start_bytes": lambda r, e: r.get("heap_measurement_start_bytes"),
    "heap_peak_bytes": lambda r, e: r.get("heap_peak_bytes"),
    "heap_delta_bytes": lambda r, e: r.get("heap_delta_bytes"),
    "gc_count": lambda r, e: r.get("gc_count_in_window"),
    "gc_pause_seconds": lambda r, e: r.get("gc_pause_seconds_in_window"),
    # Native RSS — authoritative source is native_resource, NOT top-level rss_*.
    "native_rss_start_bytes": lambda r, e: r.get("native_resource", {}).get("rss_measurement_start_bytes"),
    "native_rss_peak_bytes": lambda r, e: r.get("native_resource", {}).get("rss_measurement_peak_bytes"),
    "native_rss_delta_bytes": lambda r, e: r.get("native_resource", {}).get("rss_measurement_delta_bytes"),
}


def main():
    agg = {}
    raw = {}
    for config in CONFIGS:
        for load in LOADS:
            cell_values = {m: [] for m in METRICS}
            raw_rows = []
            for rep in REPS:
                result, env = load_run(config, load, rep)
                row = {"rep": rep}
                for metric, fn in METRICS.items():
                    v = fn(result, env)
                    cell_values[metric].append(v)
                    row[metric] = v
                raw_rows.append(row)
            key = f"{config}-{load}"
            agg[key] = {m: stats(vs) for m, vs in cell_values.items()}
            raw[key] = raw_rows

    out = {"aggregate": agg, "raw_per_run": raw}
    out_path = os.path.join(RESULTS_DIR, "_aggregated.json")
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"wrote {out_path}")
    return out


if __name__ == "__main__":
    main()
