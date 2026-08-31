#!/usr/bin/env python3
"""Phase 4 Open Formal aggregation (read-only).

Reads ONLY docs/test-results/phase4/unit9-open-formal/formal-summary.json and each cell's VALID
reps' result.json (via the "dir" recorded by the driver). Canary / Screening / Unit 8.2 calibration
are never read. Categorical state (GREEN/AMBER/RED, predicate PASS/FAIL, confirmation) is NEVER
averaged -- only counted. Numeric metrics -> median/min/max/CV, always with n.
"""
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
FORMAL_ROOT = ROOT / "docs" / "test-results" / "phase4" / "unit9-open-formal"

COMMON_NUMERIC = [
    ("client_cohort", "actual_started_rate"),
    ("client_cohort", "completion_throughput"),
    ("client_cohort", "completion_ratio"),
    ("client_latency", "completed_ttfc_p95_s"),
    ("client_latency", "completed_duration_p95_s"),
    ("platform_threads", "peak"),
    ("cpu", "avg_cores_estimate"),
    ("rss", "peak_kib"),
    ("fd", "open_peak"),
]
BACKLOG_PATHS = [("backlog", "virtual_tasks_active", "first_half_mean"),
                 ("backlog", "virtual_tasks_active", "second_half_mean"),
                 ("backlog", "gateway_active_requests", "first_half_mean"),
                 ("backlog", "gateway_active_requests", "second_half_mean")]
MODEL_NUMERIC = {
    "m1": [("implementation_specific", "executor_active_peak", "peak"),
           ("implementation_specific", "executor_queue_depth_peak", "peak"),
           ("implementation_specific", "executor_rejected_final", None)],
    "m2": [("implementation_specific", "virtual_tasks_active_peak", "peak"),
           ("implementation_specific", "tomcat_connections_current_peak", "peak"),
           ("implementation_specific", "virtual_tasks_started_final", None)],
    "m3": [("implementation_specific", "reactor_connection_active_peak", "peak"),
           ("implementation_specific", "reactor_connection_pending_peak", "peak"),
           ("implementation_specific", "max_connections_configured", None)],
}


def dig(d, *path):
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def nstats(vals):
    vals = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not vals:
        return None
    s = {"n": len(vals), "min": min(vals), "max": max(vals), "median": statistics.median(vals)}
    m = statistics.mean(vals)
    s["cv"] = (statistics.pstdev(vals) / m) if (len(vals) > 1 and m) else 0.0
    return s


def agg_cell(cid, cell):
    reps = [r for r in cell["reps"] if r.get("valid")]
    colors = {"GREEN": 0, "AMBER": 0, "RED": 0}
    cls = {}
    b_pass = s_match = rel_pass = 0
    for r in reps:
        if r.get("color") in colors:
            colors[r["color"]] += 1
        c = r.get("classification")
        if c:
            cls[c] = cls.get(c, 0) + 1
        b_pass += 1 if r.get("boundary_pass") else 0
        s_match += 1 if r.get("signature_match") else 0
        rel_pass += 1 if r.get("reliability_pass") else 0
    rjs = []
    for r in reps:
        rp = ROOT / (r.get("dir") or "") / "result.json"
        if rp.exists():
            rjs.append(json.loads(rp.read_text()))
    resource = {}
    for grp, fld in COMMON_NUMERIC:
        st = nstats([dig(rj, grp, fld) for rj in rjs])
        if st:
            resource[f"{grp}.{fld}"] = st
    for a, b, c in BACKLOG_PATHS:
        st = nstats([dig(rj, a, b, c) for rj in rjs])
        if st:
            resource[f"{a}.{b}.{c}"] = st
    for spec in MODEL_NUMERIC.get(cell["model"], []):
        if spec[2] is not None:
            key = f"{spec[0]}.{spec[1]}.{spec[2]}"
            vals = [dig(rj, spec[0], spec[1], spec[2]) for rj in rjs]
        else:
            key = f"{spec[0]}.{spec[1]}"
            vals = [dig(rj, spec[0], spec[1]) for rj in rjs]
        st = nstats(vals)
        if st:
            resource[key] = st
    return {
        "model": cell["model"], "rate": cell["rate"], "role": cell["role"],
        "expected": cell["expected"], "expected_signature": cell["expected_signature"],
        "confirmation": cell["confirmation"], "extras_used": cell["extras_used"],
        "valid_rep_count": len(reps),
        "physical_attempts": len(cell["reps"]),
        "boundary_predicate_pass_count": b_pass,
        "boundary_predicate_fail_count": len(reps) - b_pass,
        "signature_match_count": s_match,
        "reliability_pass_count": rel_pass,
        "color_distribution": colors,
        "classification_distribution": cls,
        "resource": resource,
    }


def main():
    p = FORMAL_ROOT / "formal-summary.json"
    if not p.exists():
        raise SystemExit(f"{p} not found -- run: run_phase4_open_formal.py matrix")
    summary = json.loads(p.read_text())
    out = {
        "unit": "9-open-formal",
        "stop_triggered": summary.get("stop_triggered"),
        "stop_reason": summary.get("stop_reason"),
        "provenance_drift_at_end": summary.get("provenance_drift_at_end"),
        "window": summary.get("window"),
        "frozen_order": summary.get("frozen_order"),
        "cells": {cid: agg_cell(cid, c) for cid, c in summary.get("cells", {}).items()},
    }
    # top-level Formal conclusions (wording frozen -- protocol section 5 / 30-33)
    c = out["cells"]
    f1 = c.get("F1", {}).get("confirmation")
    f2 = c.get("F2", {}).get("confirmation")
    f3 = c.get("F3", {}).get("confirmation")
    f4 = c.get("F4", {}).get("confirmation")
    m1 = ("Formal-confirmed Open MSAR bracket = [6, 7]"
          if f1 in ("CONFIRMED SUSTAINABLE", "CONFIRMED") and f2 == "CONFIRMED"
          else f"NOT fully confirmed (F1={f1}, F2={f2})")
    m2 = ("M2 Formal: Open MSAR >= 168, CONTROL_CENSORED"
          if f3 in ("CONFIRMED SUSTAINABLE", "CONFIRMED") else f"NOT confirmed (F3={f3})")
    m3 = ("M3 Formal: Open MSAR >= 168, CONTROL_CENSORED"
          if f4 in ("CONFIRMED SUSTAINABLE", "CONFIRMED") else f"NOT confirmed (F4={f4})")
    out["conclusions"] = {
        "m1": m1, "m2": m2, "m3": m3,
        "m2_vs_m3_exact_ranking": "INCONCLUSIVE (both control-censored at the single-host ceiling)",
        "note": "168 is SAFE_OPEN_MAX_SINGLE_HOST_FINAL, not a model MSAR and not an architecture ceiling.",
    }
    print(json.dumps(out, indent=2))
    (FORMAL_ROOT / "formal-aggregate.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
