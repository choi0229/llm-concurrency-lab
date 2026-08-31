#!/usr/bin/env python3
"""Phase 4 Unit 6 -- Closed Formal aggregation (read-only).

Reads ONLY docs/test-results/phase4/unit6-closed-formal/formal-summary.json and each cell's valid
reps' result.json (via the "dir" path already recorded by the driver). Canary and Screening are
never read. Never averages categorical state (GREEN/AMBER/RED) -- only replicate counts and the
frozen confirmation-rule state (docs/test-plan/phase4-closed-formal-protocol.md section 2)
represent it. Resource/latency metrics are aggregated as median/min/max/CV, always reported with
the sample size (3 or up to 5) alongside.
"""
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
FORMAL_ROOT = ROOT / "docs" / "test-results" / "phase4" / "unit6-closed-formal"

MODEL_SPECIFIC_KEYS = {
    "m1": [("implementation_specific", "executor_active_peak", "peak"),
           ("implementation_specific", "executor_queue_depth_peak", "peak"),
           ("implementation_specific", "executor_rejected_final", None)],
    "m2": [("implementation_specific", "virtual_tasks_active_peak", "peak"),
           ("implementation_specific", "virtual_tasks_started_final", None)],
    "m3": [("implementation_specific", "reactor_connection_pool", "active_peak_wave"),
           ("implementation_specific", "reactor_connection_pool", "pending_peak_wave"),
           ("implementation_specific", "reactor_connection_pool", "connection_limit_evaluable"),
           ("implementation_specific", "reactor_connection_pool", "connection_limit_binding")],
}

COMMON_NUMERIC_PATHS = [
    ("platform_threads", "peak"),
    ("cpu", "avg_cores_estimate"),
    ("rss", "start_kib"), ("rss", "peak_kib"), ("rss", "delta_kib"),
    ("fd", "open_peak"),
    ("heap", "peak"),
    ("client_latency", "completed_ttfc_p95_s"),
    ("client_latency", "completed_duration_p95_s"),
]


def dig(d, *path):
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def numeric_stats(values):
    values = [v for v in values if isinstance(v, (int, float))]
    if not values:
        return None
    stats = {"n": len(values), "min": min(values), "max": max(values), "median": statistics.median(values)}
    if len(values) > 1 and statistics.mean(values) != 0:
        stats["cv"] = statistics.pstdev(values) / statistics.mean(values)
    else:
        stats["cv"] = 0.0
    return stats


def load_summary():
    path = FORMAL_ROOT / "formal-summary.json"
    if not path.exists():
        raise SystemExit(f"formal-summary.json not found at {path} -- run the Formal driver first")
    return json.loads(path.read_text())


def aggregate_cell(cell_id, cell):
    reps = [r for r in cell["reps"] if r.get("valid")]
    n_valid = len(reps)
    color_counts = {"GREEN": 0, "AMBER": 0, "RED": 0}
    classification_counts = {}
    boundary_pass = 0
    signature_pass = 0
    resource = {}

    for r in reps:
        color = r.get("color")
        if color in color_counts:
            color_counts[color] += 1
        cls = r.get("classification")
        if cls:
            classification_counts[cls] = classification_counts.get(cls, 0) + 1
        if r.get("boundary_pass"):
            boundary_pass += 1
        if r.get("signature_pass"):
            signature_pass += 1

    result_jsons = []
    for r in reps:
        rd = r.get("dir")
        if not rd:
            continue
        rp = ROOT / rd / "result.json"
        if rp.exists():
            result_jsons.append(json.loads(rp.read_text()))

    for group, field in COMMON_NUMERIC_PATHS:
        values = [dig(rj, group, field) for rj in result_jsons]
        stats = numeric_stats(values)
        if stats:
            resource[f"{group}.{field}"] = stats

    model = cell["model"]
    for path_spec in MODEL_SPECIFIC_KEYS.get(model, []):
        if len(path_spec) == 3 and path_spec[2] is not None:
            group, subkey, leaf = path_spec
            values = [dig(rj, group, subkey, leaf) for rj in result_jsons]
            key = f"{group}.{subkey}.{leaf}"
        else:
            group, subkey = path_spec[0], path_spec[1]
            values = [dig(rj, group, subkey) for rj in result_jsons]
            key = f"{group}.{subkey}"
        bool_values = [v for v in values if isinstance(v, bool)]
        if bool_values and len(bool_values) == len(values):
            resource[key] = {"n": len(bool_values), "all_true": all(bool_values), "all_false": not any(bool_values),
                              "true_count": sum(1 for v in bool_values if v)}
            continue
        stats = numeric_stats(values)
        if stats:
            resource[key] = stats

    return {
        "role": cell.get("role"), "model": model, "N": cell.get("N"),
        "confirmation": cell.get("confirmation"), "extras_used": cell.get("extras_used", 0),
        "valid_rep_count": n_valid,
        "color_counts": color_counts,
        "boundary_predicate_pass_count": boundary_pass,
        "signature_predicate_pass_count": signature_pass,
        "classification_distribution": classification_counts,
        "resource": resource,
    }


def main():
    summary = load_summary()
    out = {
        "stop_triggered": summary.get("stop_triggered"), "stop_reason": summary.get("stop_reason"),
        "canary_pass": summary.get("canary_pass"),
        "cells": {cid: aggregate_cell(cid, cell) for cid, cell in summary.get("cells", {}).items()},
    }
    print(json.dumps(out, indent=2))
    (FORMAL_ROOT / "formal-aggregate.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
