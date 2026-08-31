#!/usr/bin/env python3
"""Phase 4.1 (Unit 6.7) -- Extended Closed Formal. Both M2 and M3 remained control-censored at
SAFE_CLOSED_MAX_EXTENDED=1120 in Extended Screening (docs/test-results/phase4/
unit6.6-extended-closed-screening/) -- Case 3 of the Phase 4.1 brief section A-18: one
censored-confirmation cell per model, 3 valid repeats each, same n=3 confirmation rule as the
original Closed Formal (docs/test-plan/phase4-closed-formal-protocol.md section 2). Uses the
EXTENDED harness/collector pair; frozen Unit 4/5/6 harness/collector untouched.
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
HARNESS = ROOT / "scripts" / "run-phase4-closed-benchmark-extended.sh"
COLLECTOR = ROOT / "scripts" / "collect_phase4_extended_closed_result.py"
HARNESS_OUT_ROOT = ROOT / "docs" / "test-results" / "phase4" / "unit4.5-extended-closed-harness"
FINAL_OUT_ROOT = ROOT / "docs" / "test-results" / "phase4" / "unit6.7-extended-closed-formal"

CELLS = {
    "EF1": {"model": "m2", "N": 1120, "role": "control-censored lower-bound confirmation (extended)"},
    "EF2": {"model": "m3", "N": 1120, "role": "control-censored lower-bound confirmation (extended)"},
}
INITIAL_ORDER = ["EF1", "EF2", "EF2", "EF1", "EF1", "EF2"]  # 3 reps each, alternating, order bias spread
NO_RETRY_KEYS = {"environment_stall_false", "pid_match", "internal_error_eq_0", "write_overflow_eq_0",
                  "m3_connection_limit_binding_absent", "m3_connection_metrics_evaluable",
                  "postflight_clean", "control_range_ok", "prewarm_contamination_ok"}
MAX_EXTRA_REPS = 2

STATE = {cid: {"reps": [], "confirmation": None} for cid in CELLS}
GLOBAL_LOG = []
STOP = {"triggered": False, "reason": None}


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    GLOBAL_LOG.append(line)


def run_point(model, N, label):
    unique_label = label
    i = 2
    while (HARNESS_OUT_ROOT / f"{model}-n{N}-{unique_label}").exists() or (FINAL_OUT_ROOT / f"{model}-n{N}-{unique_label}").exists():
        unique_label = f"{label}-{i}"
        i += 1
    log(f"RUN {model} N={N} label={unique_label}")
    proc = subprocess.run([str(HARNESS), model, str(N), unique_label], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=900)
    harness_dir = HARNESS_OUT_ROOT / f"{model}-n{N}-{unique_label}"
    if not harness_dir.exists():
        log(f"HARNESS FATAL: {model} N={N} rc={proc.returncode}\n{proc.stdout[-2000:]}\n{proc.stderr[-800:]}")
        return {"valid": False, "invalid_reasons": ["harness_no_output_dir"], "model": model, "concurrency": N}
    (harness_dir / "driver-harness-stdout.log").write_text(proc.stdout + "\n---STDERR---\n" + proc.stderr)
    cproc = subprocess.run([sys.executable, str(COLLECTOR), str(harness_dir)], capture_output=True, text=True)
    result_path = harness_dir / "result.json"
    if not result_path.exists():
        log(f"COLLECTOR FATAL: {model} N={N} stdout={cproc.stdout[-800:]} stderr={cproc.stderr[-800:]}")
        result = {"valid": False, "invalid_reasons": ["collector_failed"], "model": model, "concurrency": N}
    else:
        result = json.loads(result_path.read_text())
    final_dir = FINAL_OUT_ROOT / f"{model}-n{N}-{unique_label}"
    FINAL_OUT_ROOT.mkdir(parents=True, exist_ok=True)
    shutil.move(str(harness_dir), str(final_dir))
    result["_run_label"] = unique_label
    result["_final_dir"] = str(final_dir.relative_to(ROOT))
    return result


def trigger_stop(reason):
    STOP["triggered"] = True
    STOP["reason"] = reason
    log(f"*** EXTENDED FORMAL STOP: {reason} ***")


def execute_rep(model, N, label):
    result = run_point(model, N, label)
    if result.get("valid"):
        return result, False
    reasons = set(result.get("invalid_reasons", []))
    if reasons & NO_RETRY_KEYS:
        log(f"NO-RETRY invalid {reasons & NO_RETRY_KEYS} for {model} N={N} label={label} -- STOP")
        trigger_stop(f"{model} N={N} label={label}: no-retry INVALID ({reasons & NO_RETRY_KEYS})")
        return result, True
    log(f"INVALID (recoverable) for {model} N={N} label={label}: {reasons} -- one retry")
    retry = run_point(model, N, f"{label}-retry1")
    if retry.get("valid"):
        return retry, False
    log(f"Retry ALSO invalid for {model} N={N} label={label} -- STOP")
    trigger_stop(f"{model} N={N} label={label}: original AND retry both INVALID")
    return retry, True


def confirm_cell(cid):
    valid_reps = [r for r in STATE[cid]["reps"] if r["valid"]]
    n = len(valid_reps)
    passes = sum(1 for r in valid_reps if r["boundary_pass"])
    fails = n - passes
    if n < 3:
        return None
    if n == 3:
        if passes == 3:
            return "CONFIRMED SUSTAINABLE"
        if fails >= 2:
            return "CONFIRMED UNSTABLE"
        return "AMBIGUOUS"
    if n == 5:
        if fails <= 1:
            return "CONFIRMED SUSTAINABLE"
        if fails >= 3:
            return "CONFIRMED UNSTABLE"
        return "INCONCLUSIVE RANGE"
    return None


def run_initial():
    rep_idx = {cid: 0 for cid in CELLS}
    for cid in INITIAL_ORDER:
        if STOP["triggered"]:
            return
        rep_idx[cid] += 1
        cell = CELLS[cid]
        label = f"{cid.lower()}-rep{rep_idx[cid]}"
        result, stopped = execute_rep(cell["model"], cell["N"], label)
        if stopped:
            return
        mo = result.get("model_outcome", {}) or {}
        STATE[cid]["reps"].append({
            "label": result.get("_run_label"), "dir": result.get("_final_dir"), "valid": True,
            "color": mo.get("color"), "classification": mo.get("classification"),
            "reliability_pass": mo.get("reliability_pass"), "slo_pass": mo.get("slo_pass"),
            "boundary_pass": mo.get("color") == "GREEN",
        })
    for cid in CELLS:
        state = confirm_cell(cid)
        if state:
            STATE[cid]["confirmation"] = state
            log(f"{cid}: initial 3/3 -> {state}")


def run_extras():
    for cid in sorted(CELLS):
        if STOP["triggered"]:
            return
        if STATE[cid]["confirmation"] != "AMBIGUOUS":
            continue
        cell = CELLS[cid]
        log(f"{cid}: AMBIGUOUS -- running up to {MAX_EXTRA_REPS} extra reps")
        base = 3
        for _ in range(MAX_EXTRA_REPS):
            if STOP["triggered"]:
                return
            base += 1
            label = f"{cid.lower()}-rep{base}"
            result, stopped = execute_rep(cell["model"], cell["N"], label)
            if stopped:
                return
            mo = result.get("model_outcome", {}) or {}
            STATE[cid]["reps"].append({
                "label": result.get("_run_label"), "dir": result.get("_final_dir"), "valid": True,
                "color": mo.get("color"), "classification": mo.get("classification"),
                "reliability_pass": mo.get("reliability_pass"), "slo_pass": mo.get("slo_pass"),
                "boundary_pass": mo.get("color") == "GREEN",
            })
        STATE[cid]["confirmation"] = confirm_cell(cid) or "INCONCLUSIVE RANGE"
        log(f"{cid}: after extras -> {STATE[cid]['confirmation']}")


def main():
    FINAL_OUT_ROOT.mkdir(parents=True, exist_ok=True)
    log("=== Phase 4.1 Extended Closed Formal: START ===")
    run_initial()
    if not STOP["triggered"]:
        run_extras()
    summary = {
        "stop_triggered": STOP["triggered"], "stop_reason": STOP["reason"],
        "cells": {cid: {"role": CELLS[cid]["role"], "model": CELLS[cid]["model"], "N": CELLS[cid]["N"],
                         "confirmation": STATE[cid]["confirmation"], "reps": STATE[cid]["reps"]} for cid in CELLS},
    }
    (FINAL_OUT_ROOT / "extended-formal-summary.json").write_text(json.dumps(summary, indent=2))
    (FINAL_OUT_ROOT / "driver-log.txt").write_text("\n".join(GLOBAL_LOG))
    log("=== Phase 4.1 Extended Closed Formal: DONE ===")
    print(json.dumps({"stop_triggered": STOP["triggered"], "stop_reason": STOP["reason"]}, indent=2))


if __name__ == "__main__":
    main()
