#!/usr/bin/env python3
"""Phase 3 Unit 7 -- Formal 27-run aggregation (docs/test-plan/phase3-formal-protocol.md Sec 26/27).

Deferred deliverable, created only after all 27 runs actually completed (Sec 30). Performs:
  1. Full validity re-verification per Sec 13-A/13-B across all 27 result.json + raw artifacts
     (collect_phase3_formal_result.py's own `valid` field only checks invariant/dropped_iterations/
     preflight/postflight-cap -- write_overflow, connection-pool pending, RSS PID match, and
     unexpected outcome tags are NOT re-checked there and must be re-verified here).
  2. Frozen run-order confirmation (Sec 4) against actual process_start chronology.
  3. Per-(config,load) cell aggregation, n=3: median (Primary), min/max, CV, mean (Sec 26).
  4. Pairwise Experiment A (P3-A vs P3-B) / Experiment B (P3-B vs P3-C) comparison (Sec 27).

Usage: aggregate_phase3_formal.py [RESULTS_DIR]  (default: docs/test-results/phase3/unit7-formal)
"""
import json
import os
import re
import statistics
import sys

CONFIGS = ["A", "B", "C"]
LOADS = ["r3", "r7", "r10"]
RATES = {"r3": 3, "r7": 7, "r10": 10}

FROZEN_ORDER = [
    "p3a-r3-run1", "p3b-r3-run1", "p3c-r3-run1",
    "p3a-r7-run1", "p3b-r7-run1", "p3c-r7-run1",
    "p3a-r10-run1", "p3b-r10-run1", "p3c-r10-run1",
    "p3b-r3-run2", "p3c-r3-run2", "p3a-r3-run2",
    "p3b-r7-run2", "p3c-r7-run2", "p3a-r7-run2",
    "p3b-r10-run2", "p3c-r10-run2", "p3a-r10-run2",
    "p3c-r3-run3", "p3a-r3-run3", "p3b-r3-run3",
    "p3c-r7-run3", "p3a-r7-run3", "p3b-r7-run3",
    "p3c-r10-run3", "p3a-r10-run3", "p3b-r10-run3",
]


def load_run(results_dir, run_id):
    d = os.path.join(results_dir, run_id)
    rj_path = os.path.join(d, "result.json")
    with open(rj_path) as f:
        rj = json.load(f)
    gw_pid_path = os.path.join(d, "gateway.pid")
    gw_cmd_path = os.path.join(d, "gateway.cmdline.txt")
    gw_pid = open(gw_pid_path).read().strip() if os.path.exists(gw_pid_path) else None
    gw_cmd = open(gw_cmd_path).read().strip() if os.path.exists(gw_cmd_path) else None
    plateau_path = os.path.join(d, "counter-plateau-check.json")
    plateau = json.load(open(plateau_path)) if os.path.exists(plateau_path) else {}
    ts_path = os.path.join(d, "timestamps.json")
    ts = json.load(open(ts_path)) if os.path.exists(ts_path) else {}
    postflight_end_text = ""
    pe_path = os.path.join(d, "prometheus-measurement-end.txt")
    if os.path.exists(pe_path):
        postflight_end_text = open(pe_path).read()
    return {
        "run_id": run_id, "dir": d, "result": rj, "gw_pid": gw_pid, "gw_cmd": gw_cmd,
        "plateau": plateau, "timestamps": ts, "meas_end_snapshot": postflight_end_text,
    }


def reverify(run):
    """Sec 13-A/13-B checks NOT already covered by collect_phase3_formal_result.py's `valid` flag."""
    problems = []
    rj = run["result"]

    if rj.get("valid") is not True:
        problems.append(f"collector_valid_false:{rj.get('invalid_reason')}")

    # RSS PID verification (Sec 13-B, mandatory every run) -- gateway.pid must be a numeric PID
    # and gateway.cmdline.txt must reference a java process running an actual gateway-*.jar.
    if not run["gw_pid"] or not run["gw_pid"].isdigit():
        problems.append("rss_pid_missing_or_non_numeric")
    if not run["gw_cmd"] or "java" not in run["gw_cmd"] or ".jar" not in run["gw_cmd"]:
        problems.append("gateway_cmdline_missing_or_implausible")
    env = rj.get("environment", {})
    cfg = env.get("config")
    jar_slug = {"A": "gateway-mvc-blocking-spring5", "B": "gateway-mvc-webclient",
                "C": "gateway-webflux"}.get(cfg)
    if jar_slug and run["gw_cmd"] and jar_slug not in run["gw_cmd"]:
        problems.append(f"gateway_cmdline_jar_mismatch(expected {jar_slug})")

    # write_overflow must be 0 for A/B (Sec 13-B)
    impl = rj.get("implementation_specific") or {}
    sw = impl.get("servlet_write_path")
    if sw is not None:
        wo = sw.get("write_overflow_delta")
        if wo not in (0, 0.0, None):
            problems.append(f"write_overflow_delta={wo}")
        if wo is None:
            problems.append("write_overflow_delta_unreadable")

    # reactor_netty pending connections must stay 0 throughout for B/C (Sec 13-B)
    rn = impl.get("reactor_netty_connection_pool")
    if rn is not None:
        pend = rn.get("pending_connections_window_peak")
        if pend not in (0, 0.0, None):
            problems.append(f"connection_pending_peak={pend}")
        if pend is None:
            problems.append("connection_pending_unreadable")

    # plateau/log-gap >20s overlapping the measurement window (Sec 15) -- gaps are recorded but
    # only invalidate if they actually overlap [measurement_start, measurement_end].
    m_start = run["timestamps"].get("measurement_start", 0)
    m_end = run["timestamps"].get("measurement_end", 0)
    for key in ("gateway_log_gaps_over_20s", "mock_log_gaps_over_20s"):
        for gap in run["plateau"].get(key, []):
            problems.append(f"log_gap_{key}:{gap}")

    # unexpected outcome tags at the measurement-end Prometheus snapshot: only 'completed' and
    # 'rejected' are expected outcomes anywhere in this Formal matrix (Sec 13-B: unexpected
    # timeout/internal_error outcomes are anomaly signals).
    outcomes_seen = set(re.findall(r'gateway_requests_total\{outcome="([^"]+)"', run["meas_end_snapshot"]))
    unexpected = outcomes_seen - {"completed", "rejected"}
    if unexpected:
        problems.append(f"unexpected_outcome_tags:{sorted(unexpected)}")

    cc = rj.get("client_cohort", {})
    if cc.get("invariant_ok") is not True:
        problems.append("client_cohort_invariant_failed")
    if (cc.get("dropped_iterations") or 0) not in (0, 0.0):
        problems.append(f"dropped_iterations={cc.get('dropped_iterations')}")

    pf = rj.get("postflight", {})
    if pf.get("fail_summary"):
        problems.append(f"postflight_fail_summary:{pf.get('fail_summary')}")

    return problems


def median(vals):
    return statistics.median(vals) if vals else None


def cv(vals):
    if not vals or len(vals) < 2:
        return None
    m = statistics.mean(vals)
    if m == 0:
        return None
    return statistics.stdev(vals) / m


def cell_key(cfg, load):
    return f"p3{cfg.lower()}-{load}"


def aggregate_cell(runs):
    """runs: list of 3 result.json dicts for one (config, load) cell."""
    def get(path_fn):
        vals = [path_fn(r) for r in runs]
        return [v for v in vals if v is not None]

    thr = get(lambda r: r["throughput"]["completion_throughput_chronological"])
    rej_rate = get(lambda r: (r["client_cohort"]["client_rejected_total"] /
                               r["client_cohort"]["measurement_iterations_started_total"])
                    if r["client_cohort"]["measurement_iterations_started_total"] else None)
    ttfc_p50 = get(lambda r: r["client_latency"]["ttfc_completed_seconds"]["p50"])
    ttfc_p95 = get(lambda r: r["client_latency"]["ttfc_completed_seconds"]["p95"])
    ttfc_p99 = get(lambda r: r["client_latency"]["ttfc_completed_seconds"]["p99"])
    dur_p50 = get(lambda r: r["client_latency"]["stream_duration_completed_seconds"]["p50"])
    dur_p95 = get(lambda r: r["client_latency"]["stream_duration_completed_seconds"]["p95"])
    dur_p99 = get(lambda r: r["client_latency"]["stream_duration_completed_seconds"]["p99"])
    threads = get(lambda r: r["platform_threads"]["window_peak"])
    cpu_cores = get(lambda r: r["cpu"]["avg_cores"])
    rss_start = get(lambda r: r["native_rss"]["measurement_start_bytes"])
    rss_peak = get(lambda r: r["native_rss"]["measurement_peak_bytes"])
    rss_delta = get(lambda r: r["native_rss"]["measurement_delta_bytes"])
    heap_peak = get(lambda r: r["heap_gc"]["heap_peak_bytes"])
    gc_count = get(lambda r: r["heap_gc"]["gc_pause_count_delta"])
    gc_sec = get(lambda r: r["heap_gc"]["gc_pause_seconds_delta"])

    def stat_block(vals):
        return {"median": median(vals), "min": min(vals) if vals else None,
                "max": max(vals) if vals else None, "mean": statistics.mean(vals) if vals else None,
                "cv": cv(vals), "n": len(vals)}

    return {
        "completion_throughput": stat_block(thr),
        "rejection_rate": stat_block(rej_rate),
        "ttfc_p50_s": stat_block(ttfc_p50), "ttfc_p95_s": stat_block(ttfc_p95), "ttfc_p99_s": stat_block(ttfc_p99),
        "stream_duration_p50_s": stat_block(dur_p50), "stream_duration_p95_s": stat_block(dur_p95),
        "stream_duration_p99_s": stat_block(dur_p99),
        "platform_threads_window_peak": stat_block(threads),
        "cpu_avg_cores": stat_block(cpu_cores),
        "rss_measurement_start_bytes": stat_block(rss_start),
        "rss_measurement_peak_bytes": stat_block(rss_peak),
        "rss_measurement_delta_bytes": stat_block(rss_delta),
        "heap_peak_bytes": stat_block(heap_peak),
        "gc_pause_count_delta": stat_block(gc_count),
        "gc_pause_seconds_delta": stat_block(gc_sec),
    }


def pairwise(cell_a, cell_b, metrics):
    out = {}
    for m in metrics:
        a = cell_a[m]["median"]
        b = cell_b[m]["median"]
        if a is None or b is None:
            out[m] = None
            continue
        abs_diff = b - a
        rel_diff = (abs_diff / a * 100) if a != 0 else None
        out[m] = {"a_median": a, "b_median": b, "abs_diff_b_minus_a": abs_diff,
                   "rel_diff_pct": rel_diff}
    return out


def main():
    results_dir = sys.argv[1] if len(sys.argv) > 1 else "docs/test-results/phase3/unit7-formal"

    all_run_ids = [rid for rid in os.listdir(results_dir)
                   if re.match(r"p3[abc]-r(3|7|10)-run[123]$", rid)]
    if len(all_run_ids) != 27:
        print(f"FATAL: expected 27 run dirs, found {len(all_run_ids)}: {sorted(all_run_ids)}", file=sys.stderr)
        sys.exit(1)

    runs = {rid: load_run(results_dir, rid) for rid in all_run_ids}

    # --- run order confirmation (Sec 4) ---
    order_actual = sorted(all_run_ids, key=lambda rid: runs[rid]["timestamps"].get("process_start", 0))
    order_ok = (order_actual == FROZEN_ORDER)

    # --- full re-verification ---
    problems_by_run = {}
    for rid, run in runs.items():
        p = reverify(run)
        if p:
            problems_by_run[rid] = p

    all_clean = not problems_by_run

    print("=" * 70)
    print("VALIDITY RE-VERIFICATION")
    print("=" * 70)
    print(f"27/27 run directories present: YES")
    print(f"Actual run order matches frozen Sec-4 order: {'YES' if order_ok else 'NO -- MISMATCH'}")
    if not order_ok:
        for i, (exp, act) in enumerate(zip(FROZEN_ORDER, order_actual), 1):
            if exp != act:
                print(f"  position {i}: expected {exp}, actual {act}")
    print(f"All 27 runs clean on full Sec 13-A/13-B re-check: {'YES' if all_clean else 'NO'}")
    if problems_by_run:
        for rid, probs in problems_by_run.items():
            print(f"  {rid}: {probs}")
    print()

    if not order_ok or not all_clean:
        print("FATAL: validity re-verification failed -- refusing to aggregate. See above.", file=sys.stderr)
        sys.exit(2)

    # --- per-cell aggregation ---
    cells = {}
    for cfg in CONFIGS:
        for load in LOADS:
            key = cell_key(cfg, load)
            cell_runs = [runs[f"{key}-run{n}"]["result"] for n in (1, 2, 3)]
            cells[key] = aggregate_cell(cell_runs)

    metrics_for_pairwise = [
        "completion_throughput", "rejection_rate", "ttfc_p50_s", "ttfc_p95_s", "ttfc_p99_s",
        "stream_duration_p50_s", "stream_duration_p95_s", "stream_duration_p99_s",
        "platform_threads_window_peak", "cpu_avg_cores",
        "rss_measurement_start_bytes", "rss_measurement_peak_bytes", "rss_measurement_delta_bytes",
    ]

    experiment_a = {}  # P3-A vs P3-B
    experiment_b = {}  # P3-B vs P3-C
    for load in LOADS:
        experiment_a[load] = pairwise(cells[cell_key("A", load)], cells[cell_key("B", load)], metrics_for_pairwise)
        experiment_b[load] = pairwise(cells[cell_key("B", load)], cells[cell_key("C", load)], metrics_for_pairwise)

    output = {
        "run_order_ok": order_ok,
        "all_runs_clean": all_clean,
        "cells": cells,
        "experiment_a_A_vs_B": experiment_a,
        "experiment_b_B_vs_C": experiment_b,
    }

    out_path = os.path.join(results_dir, "aggregate-result.json")
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"Wrote {out_path}")
    print()
    print("=" * 70)
    print("PER-CELL PRIMARY MEDIANS (n=3)")
    print("=" * 70)
    for cfg in CONFIGS:
        for load in LOADS:
            key = cell_key(cfg, load)
            c = cells[key]
            print(f"{key}: thr={c['completion_throughput']['median']:.3f} rej_rate={c['rejection_rate']['median']:.4f} "
                  f"ttfc_p50={c['ttfc_p50_s']['median']:.3f}s dur_p50={c['stream_duration_p50_s']['median']:.3f}s "
                  f"threads_peak={c['platform_threads_window_peak']['median']:.1f} "
                  f"cpu_cores={c['cpu_avg_cores']['median']:.4f} "
                  f"rss_peak={c['rss_measurement_peak_bytes']['median']/1e6:.1f}MB")


if __name__ == "__main__":
    main()
