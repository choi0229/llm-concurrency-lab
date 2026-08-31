#!/usr/bin/env python3
"""Synthetic fixture tests for aggregate_phase4_closed_formal.py -- builds a fake
unit6-closed-formal/ tree (formal-summary.json + per-rep result.json), runs the real aggregator
against it, asserts specific computed fields. No Gateway/Mock/k6/subprocess involved.
"""
import json
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


def make_result(model, platform_threads_peak, cpu, rss_peak, fd_peak, ttfc_p95, dur_p95, extra_impl=None):
    impl = {}
    if model == "m1":
        impl = {"executor_active_peak": {"peak": 50}, "executor_queue_depth_peak": {"peak": 6}, "executor_rejected_final": 0.0}
    elif model == "m2":
        impl = {"virtual_tasks_active_peak": {"peak": 640}, "virtual_tasks_started_final": 645.0}
    elif model == "m3":
        impl = {"reactor_connection_pool": {"active_peak_wave": 640, "pending_peak_wave": 0,
                                             "connection_limit_evaluable": True, "connection_limit_binding": False}}
    if extra_impl:
        impl.update(extra_impl)
    return {
        "platform_threads": {"peak": platform_threads_peak},
        "cpu": {"avg_cores_estimate": cpu},
        "rss": {"start_kib": 200000, "peak_kib": rss_peak, "delta_kib": rss_peak - 200000},
        "fd": {"open_peak": fd_peak},
        "heap": {"peak": 50000000},
        "client_latency": {"completed_ttfc_p95_s": ttfc_p95, "completed_duration_p95_s": dur_p95},
        "implementation_specific": impl,
    }


def build_fixture(tmp: Path, cells):
    """cells: {cid: {"role":..., "model":..., "N":..., "confirmation":..., "extras_used":0,
    "reps": [{"valid":True,"color":...,"classification":...,"boundary_pass":...,"signature_pass":...,
    "dir_result": <result dict or None>}]}}"""
    summary = {"stop_triggered": False, "stop_reason": None, "canary_pass": True, "cells": {}}
    for cid, cell in cells.items():
        reps_out = []
        for i, rep in enumerate(cell["reps"]):
            rep_dir = tmp / f"{cid.lower()}-rep{i+1}"
            rep_dir.mkdir(parents=True, exist_ok=True)
            if rep.get("dir_result") is not None:
                (rep_dir / "result.json").write_text(json.dumps(rep["dir_result"]))
            reps_out.append({
                "label": f"rep{i+1}", "dir": str(rep_dir.relative_to(ROOT_FOR_TEST)), "valid": rep.get("valid", True),
                "color": rep.get("color"), "classification": rep.get("classification"),
                "reliability_pass": rep.get("reliability_pass"), "slo_pass": rep.get("slo_pass"),
                "boundary_pass": rep.get("boundary_pass"), "signature_pass": rep.get("signature_pass"),
            })
        summary["cells"][cid] = {"role": cell["role"], "model": cell["model"], "N": cell["N"],
                                  "confirmation": cell["confirmation"], "extras_used": cell.get("extras_used", 0),
                                  "reps": reps_out}
    (tmp / "formal-summary.json").write_text(json.dumps(summary, indent=2))


def run_aggregate(tmp: Path):
    import aggregate_phase4_closed_formal as agg
    agg.ROOT = ROOT_FOR_TEST
    agg.FORMAL_ROOT = tmp
    return agg.aggregate_cell, agg.load_summary


def main():
    global ROOT_FOR_TEST
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        ROOT_FOR_TEST = tmp  # "dir" paths in the fixture are stored relative to this and read back via ROOT/dir

        cells = {
            "F1": {"role": "MSC lower endpoint", "model": "m1", "N": 50, "confirmation": "CONFIRMED SUSTAINABLE",
                   "reps": [
                       {"color": "GREEN", "classification": "GREEN", "boundary_pass": True, "signature_pass": True,
                        "dir_result": make_result("m1", 111, 0.04, 240000, 119, 1.0, 7.9)},
                       {"color": "GREEN", "classification": "GREEN", "boundary_pass": True, "signature_pass": True,
                        "dir_result": make_result("m1", 113, 0.05, 242000, 121, 1.02, 7.95)},
                       {"color": "GREEN", "classification": "GREEN", "boundary_pass": True, "signature_pass": True,
                        "dir_result": make_result("m1", 109, 0.045, 238000, 117, 0.98, 7.85)},
                   ]},
            "F4": {"role": "MRC upper endpoint", "model": "m1", "N": 400, "confirmation": "CONFIRMED",
                   "reps": [
                       {"color": "RED", "classification": "MODEL_TIMEOUT", "boundary_pass": True, "signature_pass": True,
                        "dir_result": make_result("m1", 216, 0.06, 324000, 469, None, None)},
                       {"color": "RED", "classification": "MODEL_TIMEOUT", "boundary_pass": True, "signature_pass": True,
                        "dir_result": make_result("m1", 214, 0.061, 320000, 465, None, None)},
                       {"color": "RED", "classification": "MODEL_REJECTION", "boundary_pass": True, "signature_pass": False,
                        "dir_result": make_result("m1", 218, 0.059, 328000, 471, None, None)},
                   ]},
            "F6": {"role": "control-censored lower-bound confirmation", "model": "m3", "N": 640, "confirmation": "CONFIRMED SUSTAINABLE",
                   "reps": [
                       {"color": "GREEN", "classification": "GREEN", "boundary_pass": True, "signature_pass": True,
                        "dir_result": make_result("m3", 30, 0.10, 265000, 1326, 1.05, 8.05)},
                       {"color": "GREEN", "classification": "GREEN", "boundary_pass": True, "signature_pass": True,
                        "dir_result": make_result("m3", 30, 0.11, 267000, 1330, 1.06, 8.06)},
                       {"color": "GREEN", "classification": "GREEN", "boundary_pass": True, "signature_pass": True,
                        "dir_result": make_result("m3", 30, 0.09, 263000, 1322, 1.04, 8.04)},
                   ]},
            "F2": {"role": "MSC upper endpoint", "model": "m1", "N": 56, "confirmation": "AMBIGUOUS_INVALID_EXCLUDED_TEST",
                   "reps": [
                       {"color": "AMBER", "classification": "AMBER", "boundary_pass": True, "signature_pass": True,
                        "dir_result": make_result("m1", 112, 0.05, 244000, 165, 1.1, 7.9)},
                       {"valid": False, "color": None, "classification": None, "boundary_pass": None, "signature_pass": None,
                        "dir_result": None},
                       {"color": "AMBER", "classification": "AMBER", "boundary_pass": True, "signature_pass": True,
                        "dir_result": make_result("m1", 110, 0.048, 243000, 163, 1.08, 7.88)},
                   ]},
        }
        build_fixture(tmp, cells)
        aggregate_cell, load_summary = run_aggregate(tmp)

        summary = load_summary()

        f1 = aggregate_cell("F1", summary["cells"]["F1"])
        check("F1 valid_rep_count=3", f1["valid_rep_count"] == 3)
        check("F1 color_counts GREEN=3", f1["color_counts"]["GREEN"] == 3)
        check("F1 boundary_predicate_pass_count=3", f1["boundary_predicate_pass_count"] == 3)
        check("F1 confirmation passed through verbatim", f1["confirmation"] == "CONFIRMED SUSTAINABLE")
        check("F1 platform_threads.peak median present with n=3", f1["resource"]["platform_threads.peak"]["n"] == 3)
        check("F1 platform_threads.peak median == 111", f1["resource"]["platform_threads.peak"]["median"] == 111)
        check("F1 m1 executor_active_peak.peak stats present", "implementation_specific.executor_active_peak.peak" in f1["resource"])

        f4 = aggregate_cell("F4", summary["cells"]["F4"])
        check("F4 classification_distribution has MODEL_TIMEOUT=2, MODEL_REJECTION=1",
              f4["classification_distribution"] == {"MODEL_TIMEOUT": 2, "MODEL_REJECTION": 1})
        check("F4 signature_predicate_pass_count=2 (boundary held for all 3, signature only for 2)",
              f4["signature_predicate_pass_count"] == 2 and f4["boundary_predicate_pass_count"] == 3)
        check("F4 categorical color never averaged -- color_counts is a count dict, not a number",
              isinstance(f4["color_counts"], dict))

        f6 = aggregate_cell("F6", summary["cells"]["F6"])
        check("F6 m3 connection_limit_evaluable all_true", f6["resource"]["implementation_specific.reactor_connection_pool.connection_limit_evaluable"]["all_true"] is True)
        check("F6 m3 connection_limit_binding all_false", f6["resource"]["implementation_specific.reactor_connection_pool.connection_limit_binding"]["all_false"] is True)

        f2 = aggregate_cell("F2", summary["cells"]["F2"])
        check("F2 invalid rep excluded from valid_rep_count (2, not 3)", f2["valid_rep_count"] == 2)
        check("F2 invalid rep excluded from resource aggregation sample size (n=2)", f2["resource"]["platform_threads.peak"]["n"] == 2)

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s): {failures}")
        sys.exit(1)
    print("ALL FORMAL AGGREGATE SYNTHETIC TESTS PASSED")


if __name__ == "__main__":
    main()
