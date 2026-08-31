#!/usr/bin/env python3
"""Phase 4.1 (Unit 6.6) -- Extended Closed screening driver for M2/M3 only (M1 not re-tested,
already known-RED at N=400 in the original Closed Formal). Uses the EXTENDED harness/collector
pair (run-phase4-closed-benchmark-extended.sh + collect_phase4_extended_closed_result.py) --
frozen Unit 4/5/6 harness/collector are never invoked or modified by this driver.

Candidate levels: 800, 1000, SAFE_CLOSED_MAX_EXTENDED=1120 (docs/test-results/phase4/
unit6.5-extended-closed-control/SUMMARY.md). Interleaved per Phase 4.1 brief section A-10.
Same VALID/INVALID gate, GREEN/AMBER/RED semantics, and midpoint-refinement rule (<=20% relative
width or 4 extra points) as the original Unit 5 screening driver -- reused conceptually, not by
importing that module (that module is specific to the original 640-level geometric progression and
M1/M2/M3 three-way interleave; this is a narrower, 2-model, 3-level driver).
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
FINAL_OUT_ROOT = ROOT / "docs" / "test-results" / "phase4" / "unit6.6-extended-closed-screening"
SAFE_CLOSED_MAX_EXTENDED = 1120

INITIAL_LEVELS = [
    (800, ["m2", "m3"]),
    (1000, ["m3", "m2"]),
    (1120, ["m2", "m3"]),
]

NO_RETRY_KEYS = {"environment_stall_false", "pid_match", "internal_error_eq_0", "write_overflow_eq_0",
                  "m3_connection_limit_binding_absent", "m3_connection_metrics_evaluable",
                  "postflight_clean", "control_range_ok", "prewarm_contamination_ok"}
KNOWN_RED = {"MODEL_REJECTION", "MODEL_TIMEOUT"}
MAX_REFINEMENT_POINTS = 4
BRACKET_WIDTH_TARGET = 0.20

STATE = {m: {"points": [], "active": True, "green_high": None, "non_green_low": None,
             "reliability_high": None, "red_low": None, "red_classification": None,
             "msc_censored": False, "mrc_censored": False, "refinement_count": 0}
         for m in ["m2", "m3"]}
GLOBAL_LOG = []
STOP = {"triggered": False, "reason": None}


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    GLOBAL_LOG.append(line)


def point_cache_lookup(model, N):
    for p in STATE[model]["points"]:
        if p["N"] == N and p["valid"]:
            return p
    return None


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
        log(f"HARNESS FATAL (no output dir): {model} N={N} rc={proc.returncode}\n{proc.stdout[-2000:]}\n{proc.stderr[-800:]}")
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
    log(f"*** EXTENDED SCREENING STOP: {reason} ***")


def record_point(model, result):
    entry = {"N": result.get("concurrency"), "label": result.get("_run_label"), "dir": result.get("_final_dir"),
              "valid": result.get("valid", False), "invalid_reasons": result.get("invalid_reasons", [])}
    mo = result.get("model_outcome", {})
    entry["color"] = mo.get("color")
    entry["classification"] = mo.get("classification")
    entry["reliability_pass"] = mo.get("reliability_pass")
    entry["slo_pass"] = mo.get("slo_pass")
    STATE[model]["points"].append(entry)
    return entry


def update_brackets(model, entry):
    if not entry["valid"]:
        return
    N = entry["N"]
    st = STATE[model]
    if entry["color"] == "GREEN":
        st["green_high"] = N if st["green_high"] is None else max(st["green_high"], N)
        st["reliability_high"] = N if st["reliability_high"] is None else max(st["reliability_high"], N)
    else:
        st["non_green_low"] = N if st["non_green_low"] is None else min(st["non_green_low"], N)
        if entry["reliability_pass"]:
            st["reliability_high"] = N if st["reliability_high"] is None else max(st["reliability_high"], N)
        if entry["color"] == "RED" and st["red_low"] is None:
            st["red_low"] = N
            st["red_classification"] = entry["classification"]


def execute_point(model, N, label):
    result = run_point(model, N, label)
    if result.get("valid"):
        record_point(model, result)
        return result, False
    reasons = set(result.get("invalid_reasons", []))
    if reasons & NO_RETRY_KEYS:
        log(f"NO-RETRY invalid reason(s) {reasons & NO_RETRY_KEYS} for {model} N={N} -- STOP")
        record_point(model, result)
        trigger_stop(f"{model} N={N}: no-retry INVALID ({reasons & NO_RETRY_KEYS})")
        return result, True
    log(f"INVALID (recoverable class) for {model} N={N}: {reasons} -- one retry allowed")
    retry = run_point(model, N, f"{label}-retry1")
    if retry.get("valid"):
        record_point(model, retry)
        return retry, False
    log(f"Retry ALSO invalid for {model} N={N}: {retry.get('invalid_reasons')} -- STOP")
    record_point(model, retry)
    trigger_stop(f"{model} N={N}: original AND retry both INVALID")
    return retry, True


def initial_pass():
    for N, order in INITIAL_LEVELS:
        for model in order:
            if STOP["triggered"]:
                return
            st = STATE[model]
            if not st["active"]:
                log(f"SKIP {model} N={N}: model already inactive (known-RED or censored)")
                continue
            cached = point_cache_lookup(model, N)
            if cached:
                entry = cached
            else:
                result, stopped = execute_point(model, N, "screen1")
                if stopped:
                    return
                entry = st["points"][-1]
                update_brackets(model, entry)
            if entry["valid"] and entry["color"] == "RED":
                if entry["classification"] in KNOWN_RED:
                    log(f"{model} N={N}: known RED ({entry['classification']}) -- stop escalating")
                    st["active"] = False
            if N == SAFE_CLOSED_MAX_EXTENDED and entry["valid"] and entry["color"] in ("GREEN", "AMBER"):
                st["active"] = False


def refine(model):
    st = STATE[model]
    if st["red_low"] is not None and st["red_classification"] in KNOWN_RED:
        low, high = st["green_high"], st["non_green_low"]
        while low is not None and high is not None and st["refinement_count"] < MAX_REFINEMENT_POINTS:
            width = (high - low) / low if low else 1.0
            if width <= BRACKET_WIDTH_TARGET:
                break
            mid = (low + high) // 2
            if mid in (low, high):
                break
            cached = point_cache_lookup(model, mid)
            if cached:
                entry = cached
            else:
                result, stopped = execute_point(model, mid, f"msc-refine{st['refinement_count']+1}")
                if stopped:
                    return
                entry = st["points"][-1]
                update_brackets(model, entry)
            st["refinement_count"] += 1
            if entry["valid"] and entry["color"] == "GREEN":
                low = mid
            else:
                high = mid
        st["green_high"], st["non_green_low"] = low, high
    else:
        if st["green_high"] is not None and st["green_high"] >= SAFE_CLOSED_MAX_EXTENDED:
            st["msc_censored"] = True
            st["mrc_censored"] = True
            log(f"{model}: MSC/MRC >= {SAFE_CLOSED_MAX_EXTENDED}, CONTROL_CENSORED")


def resume_from_existing():
    """Picks up already-completed VALID points from a prior invocation (same pattern as the Unit 5
    screening driver's resume_from_existing()) so a fixed bug/config issue doesn't force re-running
    a point whose underlying load already executed correctly."""
    if not FINAL_OUT_ROOT.exists():
        return
    for d in sorted(FINAL_OUT_ROOT.iterdir()):
        if not d.is_dir() or "-n" not in d.name or "-screen1" not in d.name:
            continue
        result_path = d / "result.json"
        if not result_path.exists():
            continue
        try:
            result = json.loads(result_path.read_text())
        except json.JSONDecodeError:
            continue
        model = result.get("model")
        if model not in STATE:
            continue
        result["_run_label"] = d.name.split(f"{model}-n{result.get('concurrency')}-", 1)[-1]
        result["_final_dir"] = str(d.relative_to(ROOT))
        entry = record_point(model, result)
        update_brackets(model, entry)
        log(f"RESUMED {model} N={entry['N']} from existing valid artifact ({d.name}): color={entry.get('color')}")
    for model in STATE:
        st = STATE[model]
        if st["red_low"] is not None and st["red_classification"] in KNOWN_RED:
            st["active"] = False
        if any(p["N"] == SAFE_CLOSED_MAX_EXTENDED and p["valid"] and p["color"] in ("GREEN", "AMBER") for p in st["points"]):
            st["active"] = False


def main():
    FINAL_OUT_ROOT.mkdir(parents=True, exist_ok=True)
    log("=== Phase 4.1 Extended Closed Screening: START ===")
    resume_from_existing()
    initial_pass()
    if not STOP["triggered"]:
        for model in ["m2", "m3"]:
            refine(model)
    summary = {"stop_triggered": STOP["triggered"], "stop_reason": STOP["reason"], "models": {}}
    for model in ["m2", "m3"]:
        st = STATE[model]
        summary["models"][model] = {
            "points": st["points"], "green_high": st["green_high"], "non_green_low": st["non_green_low"],
            "reliability_high": st["reliability_high"], "red_low": st["red_low"],
            "red_classification": st["red_classification"], "msc_censored": st["msc_censored"],
            "mrc_censored": st["mrc_censored"], "refinement_count": st["refinement_count"],
        }
    (FINAL_OUT_ROOT / "extended-screening-summary.json").write_text(json.dumps(summary, indent=2))
    (FINAL_OUT_ROOT / "driver-log.txt").write_text("\n".join(GLOBAL_LOG))
    log("=== Phase 4.1 Extended Closed Screening: DONE ===")
    print(json.dumps({"stop_triggered": STOP["triggered"], "stop_reason": STOP["reason"]}, indent=2))


if __name__ == "__main__":
    main()
