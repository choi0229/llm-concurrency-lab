#!/usr/bin/env python3
"""Phase 4 Unit 6 -- Closed Formal confirmation driver.

Orchestrates the FROZEN harness (run-phase4-closed-benchmark.sh + collect_phase4_closed_result.py
+ check_phase4_stall.py + 04-phase4-closed-wave.js + prometheus-phase4.yml) -- never modifies any
of them -- to run the 6-cell, 18-initial-run Formal matrix frozen in
docs/test-plan/phase4-closed-formal-protocol.md. Does NOT recompute GREEN/AMBER/RED itself; reads
model_outcome verbatim from the frozen collector's result.json. Every design decision here
(cells, N values, deterministic run order, confirmation rule, invalid/retry policy, Canary) is
frozen in that document -- this file only executes it.
"""
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
HARNESS = ROOT / "scripts" / "run-phase4-closed-benchmark.sh"
COLLECTOR = ROOT / "scripts" / "collect_phase4_closed_result.py"
HARNESS_OUT_ROOT = ROOT / "docs" / "test-results" / "phase4" / "unit4-closed-harness"  # frozen script's hardcoded output dir
FINAL_OUT_ROOT = ROOT / "docs" / "test-results" / "phase4" / "unit6-closed-formal"

# ---- Formal cells (docs/test-plan/phase4-closed-formal-protocol.md section 4) ----
FORMAL_CELLS = {
    "F1": {"model": "m1", "N": 50, "role": "MSC lower endpoint", "expected_state_name": "SUSTAINABLE",
           "boundary": lambda mo: mo.get("color") == "GREEN",
           "signature": lambda mo: mo.get("color") == "GREEN", "signature_name": "GREEN"},
    "F2": {"model": "m1", "N": 56, "role": "MSC upper endpoint", "expected_state_name": "BOUNDARY",
           "boundary": lambda mo: mo.get("color") != "GREEN",
           "signature": lambda mo: mo.get("color") == "AMBER", "signature_name": "AMBER"},
    "F3": {"model": "m1", "N": 350, "role": "MRC lower endpoint", "expected_state_name": "BOUNDARY",
           "boundary": lambda mo: mo.get("reliability_pass") is True,
           "signature": lambda mo: mo.get("color") == "AMBER", "signature_name": "AMBER"},
    "F4": {"model": "m1", "N": 400, "role": "MRC upper endpoint", "expected_state_name": "BOUNDARY",
           "boundary": lambda mo: mo.get("reliability_pass") is False,
           "signature": lambda mo: mo.get("classification") == "MODEL_TIMEOUT", "signature_name": "MODEL_TIMEOUT"},
    "F5": {"model": "m2", "N": 640, "role": "control-censored lower-bound confirmation", "expected_state_name": "SUSTAINABLE",
           "boundary": lambda mo: mo.get("color") == "GREEN",
           "signature": lambda mo: mo.get("color") == "GREEN", "signature_name": "GREEN"},
    "F6": {"model": "m3", "N": 640, "role": "control-censored lower-bound confirmation", "expected_state_name": "SUSTAINABLE",
           "boundary": lambda mo: mo.get("color") == "GREEN",
           "signature": lambda mo: mo.get("color") == "GREEN", "signature_name": "GREEN"},
}

# Frozen literal order (protocol section 16). Each cell appears exactly once per block.
INITIAL_ORDER = (
    ["F1", "F5", "F2", "F6", "F3", "F4"] +
    ["F6", "F3", "F1", "F4", "F5", "F2"] +
    ["F2", "F4", "F6", "F1", "F3", "F5"]
)
assert len(INITIAL_ORDER) == 18
for _cid in FORMAL_CELLS:
    assert INITIAL_ORDER.count(_cid) == 3

CANARY_CELL = {"model": "m3", "N": 640}

NO_RETRY_KEYS = {"environment_stall_false", "pid_match", "internal_error_eq_0", "write_overflow_eq_0",
                  "m3_connection_limit_binding_absent", "m3_connection_metrics_evaluable",
                  "postflight_clean", "control_range_ok", "prewarm_contamination_ok"}

MAX_EXTRA_REPS = 2

STATE = {cid: {"reps": [], "confirmation": None, "extras_used": 0} for cid in FORMAL_CELLS}
GLOBAL_LOG = []
STOP = {"triggered": False, "reason": None}


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    GLOBAL_LOG.append(line)


def sha256_of(path):
    p = Path(path)
    if not p.exists():
        return None
    return hashlib.sha256(p.read_bytes()).hexdigest()


def record_provenance(filename):
    """Writes a NEW, separately-named provenance snapshot -- never overwrites a prior one, so the
    Canary-time and Formal-start-time hashes both survive on disk (Unit 6 orchestration audit
    section 5: '과거 hash를 덮어써서 history를 잃지 않는다')."""
    prov = {
        "m1_jar_sha256": sha256_of(ROOT / "gateway-phase4-platform-queue/build/libs/gateway-phase4-platform-queue-0.1.0.jar"),
        "m2_jar_sha256": sha256_of(ROOT / "gateway-phase4-virtual-thread/build/libs/gateway-phase4-virtual-thread-0.1.0.jar"),
        "m3_jar_sha256": sha256_of(ROOT / "gateway-phase4-webflux/build/libs/gateway-phase4-webflux-0.1.0.jar"),
        "blocking_mock_llm_relay_sha256": sha256_of(ROOT / "gateway-phase4-platform-queue/src/main/java/com/llmconcurrencylab/phase4/common/BlockingMockLlmRelay.java"),
        "collector_sha256": sha256_of(COLLECTOR),
        "stall_checker_sha256": sha256_of(ROOT / "scripts/check_phase4_stall.py"),
        "k6_closed_wave_sha256": sha256_of(ROOT / "load-test-k6/scenarios/04-phase4-closed-wave.js"),
        "prometheus_config_sha256": sha256_of(ROOT / "monitoring/prometheus/prometheus-phase4.yml"),
        "run_harness_sha256": sha256_of(HARNESS),
        "formal_driver_sha256": sha256_of(__file__),
    }
    FINAL_OUT_ROOT.mkdir(parents=True, exist_ok=True)
    (FINAL_OUT_ROOT / filename).write_text(json.dumps(prov, indent=2))
    log(f"Provenance recorded ({filename}): {json.dumps(prov)}")
    return prov


def load_existing_canary():
    path = FINAL_OUT_ROOT / "canary-result.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def verify_existing_canary(data):
    """Fail-closed check (orchestration audit section 2): an existing Canary artifact is reused
    to skip re-running Canary load ONLY if it is present, PASSed, and unambiguously for the frozen
    Canary cell (model=m3, N=640) with every one of its own recorded checks true. Anything else ->
    reject, caller must not proceed by silently running a fresh Canary in --formal-only mode."""
    if not data:
        return False, "no existing canary-result.json"
    if not data.get("pass"):
        return False, "existing canary-result.json has pass=false"
    checks = data.get("checks", {})
    if not checks or not all(checks.values()):
        return False, f"existing canary checks not all true: {checks}"
    result = data.get("result", {}) or {}
    if result.get("model") != CANARY_CELL["model"] or result.get("concurrency") != CANARY_CELL["N"]:
        return False, f"existing canary is for wrong model/N: model={result.get('model')} N={result.get('concurrency')}"
    return True, "existing PASS canary verified"


def run_point(model, N, label):
    """Runs the frozen harness once, collects, relocates artifacts under unit6-closed-formal/ --
    identical pattern to the Unit 5 screening driver's run_point(), pure filesystem move, never
    touches the frozen harness/collector."""
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
        log(f"HARNESS FATAL (no output dir): {model} N={N} rc={proc.returncode}\n{proc.stdout[-3000:]}\n{proc.stderr[-1000:]}")
        return {"valid": False, "invalid_reasons": ["harness_no_output_dir"], "model": model, "concurrency": N,
                "run_label": unique_label, "harness_stdout_tail": proc.stdout[-3000:]}, unique_label
    (harness_dir / "driver-harness-stdout.log").write_text(proc.stdout + "\n---STDERR---\n" + proc.stderr)
    cproc = subprocess.run([sys.executable, str(COLLECTOR), str(harness_dir)], capture_output=True, text=True)
    result_path = harness_dir / "result.json"
    if not result_path.exists():
        log(f"COLLECTOR FATAL: {model} N={N} stdout={cproc.stdout[-1000:]} stderr={cproc.stderr[-1000:]}")
        result = {"valid": False, "invalid_reasons": ["collector_failed"], "model": model, "concurrency": N, "run_label": unique_label}
    else:
        result = json.loads(result_path.read_text())
    final_dir = FINAL_OUT_ROOT / f"{model}-n{N}-{unique_label}"
    FINAL_OUT_ROOT.mkdir(parents=True, exist_ok=True)
    shutil.move(str(harness_dir), str(final_dir))
    result["_run_label"] = unique_label
    result["_final_dir"] = str(final_dir.relative_to(ROOT))
    return result, unique_label


def trigger_stop(reason):
    STOP["triggered"] = True
    STOP["reason"] = reason
    log(f"*** UNIT 6 FORMAL STOP: {reason} ***")


def execute_rep(model, N, label):
    """Runs one rep with the frozen invalid/retry policy (protocol section 8). Returns
    (result_dict_or_None, stopped_bool)."""
    result, _ = run_point(model, N, label)
    if result.get("valid"):
        return result, False

    reasons = set(result.get("invalid_reasons", []))
    if reasons & NO_RETRY_KEYS:
        log(f"NO-RETRY invalid reason(s) {reasons & NO_RETRY_KEYS} for {model} N={N} label={label} -- Formal STOP")
        trigger_stop(f"{model} N={N} label={label}: no-retry INVALID ({reasons & NO_RETRY_KEYS})")
        return result, True

    log(f"INVALID (recoverable class) for {model} N={N} label={label}: {reasons} -- one retry allowed")
    retry, _ = run_point(model, N, f"{label}-retry1")
    if retry.get("valid"):
        log(f"Retry succeeded for {model} N={N} label={label}")
        return retry, False

    log(f"Retry ALSO invalid for {model} N={N} label={label}: {retry.get('invalid_reasons')} -- Formal STOP")
    trigger_stop(f"{model} N={N} label={label}: original AND retry both INVALID")
    return retry, True


def run_canary():
    log("=== CANARY: M3 N=640 (excluded from the 18-run matrix) ===")
    result, _ = run_point(CANARY_CELL["model"], CANARY_CELL["N"], "canary")
    checks = {
        "valid": result.get("valid") is True,
        "target_reached": (result.get("client_cohort", {}) or {}).get("target_concurrency") == 640,
        "actual_started_640": (result.get("client_cohort", {}) or {}).get("actual_started") == 640,
        "invariant_ok": (result.get("client_cohort", {}) or {}).get("invariant_ok") is True,
        "color_green": (result.get("model_outcome", {}) or {}).get("color") == "GREEN",
        "no_stall": (result.get("stall_check", {}) or {}).get("stall_detected") is False,
        "postflight_clean": result.get("postflight_clean") is True,
        "connection_limit_evaluable": ((result.get("implementation_specific", {}) or {}).get("reactor_connection_pool", {}) or {}).get("connection_limit_evaluable") is True,
        "connection_limit_binding_absent": ((result.get("implementation_specific", {}) or {}).get("reactor_connection_pool", {}) or {}).get("connection_limit_binding") is False,
    }
    canary_pass = all(checks.values())
    log(f"CANARY checks: {json.dumps(checks)} -> {'PASS' if canary_pass else 'FAIL'}")
    (FINAL_OUT_ROOT / "canary-result.json").write_text(json.dumps({"pass": canary_pass, "checks": checks, "result": result}, indent=2))
    if not canary_pass:
        trigger_stop(f"CANARY FAIL: {checks}")
    return canary_pass


def confirm_cell(cid):
    """Frozen confirmation rule (protocol section 2), applied to the boundary predicate."""
    cell = FORMAL_CELLS[cid]
    valid_reps = [r for r in STATE[cid]["reps"] if r["valid"]]
    n = len(valid_reps)
    passes = sum(1 for r in valid_reps if r["boundary_pass"])
    fails = n - passes
    sustainable = cell["expected_state_name"] == "SUSTAINABLE"

    def label(is_pass):
        if is_pass:
            return "CONFIRMED SUSTAINABLE" if sustainable else "CONFIRMED"
        return "CONFIRMED UNSTABLE" if sustainable else "NOT CONFIRMED"

    if n < 3:
        return None  # not enough reps yet
    if n == 3:
        if passes == 3:
            return label(True)
        if fails >= 2:
            return label(False)
        return "AMBIGUOUS"
    if n == 5:
        # Unit 5.5 final closure (docs/test-plan/phase4-closed-formal-protocol.md section 2). The
        # only reachable AMBIGUOUS seed under the n==3 rule above is exactly (2 pass, 1 fail) --
        # (1 pass, 2 fail) and (0, 3) are already FAIL-decisive at n==3, and (3, 0) is already
        # PASS-decisive there. So after 2 extra reps, n==5 can only ever land on exactly one of
        # three reachable states -- 5-0 and 0-5 are mathematically unreachable from that seed.
        # Frozen, symmetric resolution over those three:
        #   4 PASS / 1 FAIL -> PASS-decisive  (sustainable evidence clearly dominant)
        #   3 PASS / 2 FAIL -> INCONCLUSIVE RANGE (genuinely still mixed)
        #   2 PASS / 3 FAIL -> FAIL-decisive  (unstable evidence clearly dominant)
        if fails <= 1:
            return label(True)
        if fails >= 3:
            return label(False)
        return "INCONCLUSIVE RANGE"
    return None


def run_initial_matrix():
    rep_index = {cid: 0 for cid in FORMAL_CELLS}
    for cid in INITIAL_ORDER:
        if STOP["triggered"]:
            return
        rep_index[cid] += 1
        cell = FORMAL_CELLS[cid]
        label = f"{cid.lower()}-rep{rep_index[cid]}"
        result, stopped = execute_rep(cell["model"], cell["N"], label)
        if stopped:
            return
        mo = result.get("model_outcome", {}) or {}
        STATE[cid]["reps"].append({
            "label": result.get("_run_label"), "dir": result.get("_final_dir"), "valid": True,
            "color": mo.get("color"), "classification": mo.get("classification"),
            "reliability_pass": mo.get("reliability_pass"), "slo_pass": mo.get("slo_pass"),
            "boundary_pass": bool(cell["boundary"](mo)), "signature_pass": bool(cell["signature"](mo)),
        })
    for cid in FORMAL_CELLS:
        state = confirm_cell(cid)
        if state:
            STATE[cid]["confirmation"] = state
            log(f"{cid}: initial 3/3 -> {state}")


def run_extras():
    """Extras only after all 18 initial reps complete, cell-ID order, max 2 per ambiguous cell
    (protocol section 17)."""
    for cid in sorted(FORMAL_CELLS):
        if STOP["triggered"]:
            return
        if STATE[cid]["confirmation"] != "AMBIGUOUS":
            continue
        cell = FORMAL_CELLS[cid]
        log(f"{cid}: AMBIGUOUS after initial 3 -- running up to {MAX_EXTRA_REPS} extra reps")
        base_rep = 3
        for i in range(MAX_EXTRA_REPS):
            if STOP["triggered"]:
                return
            base_rep += 1
            label = f"{cid.lower()}-rep{base_rep}"
            result, stopped = execute_rep(cell["model"], cell["N"], label)
            if stopped:
                return
            mo = result.get("model_outcome", {}) or {}
            STATE[cid]["reps"].append({
                "label": result.get("_run_label"), "dir": result.get("_final_dir"), "valid": True,
                "color": mo.get("color"), "classification": mo.get("classification"),
                "reliability_pass": mo.get("reliability_pass"), "slo_pass": mo.get("slo_pass"),
                "boundary_pass": bool(cell["boundary"](mo)), "signature_pass": bool(cell["signature"](mo)),
            })
            STATE[cid]["extras_used"] += 1
        state = confirm_cell(cid)
        STATE[cid]["confirmation"] = state or "INCONCLUSIVE RANGE"
        log(f"{cid}: after extras -> {STATE[cid]['confirmation']}")


def _stop_and_write(canary_pass, canary_only, extra_fields=None):
    (FINAL_OUT_ROOT / "formal-summary.json").write_text(json.dumps(
        {"stop_triggered": STOP["triggered"], "stop_reason": STOP["reason"], "canary_pass": canary_pass,
         "canary_only_requested": canary_only, "cells": {}, **(extra_fields or {})}, indent=2))
    (FINAL_OUT_ROOT / "driver-log.txt").write_text("\n".join(GLOBAL_LOG))
    print(json.dumps({"stop_triggered": STOP["triggered"], "stop_reason": STOP["reason"], "canary_pass": canary_pass}, indent=2))


def main():
    """Three explicit modes (Unit 6 orchestration audit): `--canary-only` runs Canary exactly once
    and stops (never touches the 18-run matrix). `--formal-only` NEVER calls run_canary() -- it
    fail-closed-verifies an existing PASS canary-result.json for the frozen Canary cell and stops
    without starting the matrix if that verification fails; it does not fall back to running a
    fresh Canary. Plain (no flag) is the only mode that may run a fresh Canary followed immediately
    by the matrix, for a from-scratch environment with no prior Canary artifact at all -- even then
    it reuses an existing PASS canary-result.json instead of re-running Canary load if one is
    already present, so this mode is also safe to invoke after a Canary has already passed."""
    canary_only = "--canary-only" in sys.argv
    formal_only = "--formal-only" in sys.argv
    FINAL_OUT_ROOT.mkdir(parents=True, exist_ok=True)
    log("=== Phase 4 Unit 6 Closed Formal: START ===" + (" (--canary-only)" if canary_only else " (--formal-only)" if formal_only else ""))

    if formal_only:
        record_provenance("formal-provenance-formal-start.json")
        ok, reason = verify_existing_canary(load_existing_canary())
        if not ok:
            log(f"--formal-only: existing Canary verification FAILED ({reason}) -- Formal STOP, matrix NOT started")
            trigger_stop(f"--formal-only precondition failed: {reason}")
            _stop_and_write(False, canary_only)
            return
        log(f"--formal-only: existing PASS Canary verified ({reason}) -- run_canary() NOT called, proceeding directly to the 18-run matrix")
        canary_pass = True
    elif canary_only:
        record_provenance("formal-provenance-canary.json")
        canary_pass = run_canary()
        if not canary_pass:
            log("=== Phase 4 Unit 6 Closed Formal: STOPPED AFTER CANARY ===")
            _stop_and_write(False, canary_only)
            return
        log("=== Phase 4 Unit 6 Closed Formal: STOPPED AFTER CANARY (--canary-only, 18-run matrix not started) ===")
        _stop_and_write(True, canary_only)
        return
    else:
        ok, reason = verify_existing_canary(load_existing_canary())
        record_provenance("formal-provenance-formal-start.json" if ok else "formal-provenance-canary.json")
        if ok:
            log(f"Existing PASS Canary verified ({reason}) -- run_canary() NOT called")
            canary_pass = True
        else:
            log(f"No usable existing Canary ({reason}) -- running a fresh Canary")
            canary_pass = run_canary()
        if not canary_pass:
            log("=== Phase 4 Unit 6 Closed Formal: STOPPED AFTER CANARY ===")
            _stop_and_write(False, canary_only)
            return

    run_initial_matrix()
    if not STOP["triggered"]:
        run_extras()

    summary = {
        "stop_triggered": STOP["triggered"], "stop_reason": STOP["reason"], "canary_pass": True,
        "cells": {cid: {"role": FORMAL_CELLS[cid]["role"], "model": FORMAL_CELLS[cid]["model"], "N": FORMAL_CELLS[cid]["N"],
                         "confirmation": STATE[cid]["confirmation"], "extras_used": STATE[cid]["extras_used"],
                         "reps": STATE[cid]["reps"]} for cid in FORMAL_CELLS},
    }
    (FINAL_OUT_ROOT / "formal-summary.json").write_text(json.dumps(summary, indent=2))
    (FINAL_OUT_ROOT / "driver-log.txt").write_text("\n".join(GLOBAL_LOG))
    log("=== Phase 4 Unit 6 Closed Formal: DONE ===")
    print(json.dumps({"stop_triggered": STOP["triggered"], "stop_reason": STOP["reason"]}, indent=2))


if __name__ == "__main__":
    main()
