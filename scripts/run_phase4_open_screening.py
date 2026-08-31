#!/usr/bin/env python3
"""Phase 4 Part B (Unit 7) -- Open-model screening driver, M1/M2/M3. Adaptive geometric rates
capped at SAFE_OPEN_MAX=256 (docs/decisions/phase4-scalability-definition.md sections 10/16-2),
screening windows warmup=60s/measurement=120s (docs/test-plan/phase4-open-screening-protocol.md
section 2.1). Same VALID/INVALID gate, GREEN/AMBER/RED, and midpoint-refinement machinery as the
Closed screening driver, adapted to rates instead of concurrency levels.
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
HARNESS = ROOT / "scripts" / "run-phase4-open-benchmark.sh"
COLLECTOR = ROOT / "scripts" / "collect_phase4_open_result.py"
# Phase 4 Open Unit 8 -- canonical Screening under the FINAL frozen control contract declared in
# Unit 7.5 (docs/test-results/phase4/unit7.5-client-lifecycle-fix/SUMMARY.md section 29):
# client.close() lifecycle fix, dropped_iterations==0 mandatory, OPEN_TOMCAT_MAX_CONNECTIONS=3200,
# final VU sizing, UNEXPLAINED full-STOP. Fresh output root -- Unit 7.x screening/regression/
# diagnostic artifacts are history/evidence only, never reused as canonical points here.
SCREENING_EPOCH = "open-final-client-lifecycle-v1"
HARNESS_OUT_ROOT = ROOT / "docs" / "test-results" / "phase4" / "unit8-open-harness"
FINAL_OUT_ROOT = ROOT / "docs" / "test-results" / "phase4" / "unit8-open-screening"
SAFE_OPEN_MAX = 256
WARMUP_SEC = 60
MEASUREMENT_SEC = 120

INITIAL_LEVELS = [
    (2, ["m1", "m2", "m3"]),
    (4, ["m2", "m3", "m1"]),
    (10, ["m3", "m1", "m2"]),
    (20, ["m1", "m2", "m3"]),
    (40, ["m2", "m3", "m1"]),
    (80, ["m3", "m1", "m2"]),
    (160, ["m1", "m2", "m3"]),
    (256, ["m2", "m3", "m1"]),
]

KNOWN_RED = {"MODEL_REJECTION", "MODEL_TIMEOUT"}
# Mirrors scripts/run_phase4_closed_screening.py's UNEXPLAINED set exactly (same four
# classifications). Unlike KNOWN_RED (which only deactivates the one model), any of these must halt
# the ENTIRE driver for investigation before further automatic progression -- see
# docs/test-results/phase4/unit7.1-m2-r160-frontdoor-forensics/driver-stop-regression.md for the
# incident (M2 R=160 UNCLASSIFIED_MODEL_FAILURE) that exposed this gap: the driver had no such check
# at all and proceeded to run M3 R=160 immediately afterward.
UNEXPLAINED = {"UPSTREAM_ERROR_UNCONFIRMED_CAUSE", "UNEXPECTED_CLIENT_DISCONNECT",
               "UNCLASSIFIED_MODEL_FAILURE", "INTERNAL_ERROR_BUG"}
NO_RETRY_KEYS = {"environment_stall_false", "pid_match", "cpu_metric_parsable", "fd_metric_present",
                  "postflight_clean", "control_range_ok", "clock_integrity_ok", "measurement_window_available",
                  "tomcat_connections_non_binding"}
MAX_REFINEMENT_POINTS = 4
BRACKET_WIDTH_TARGET = 0.20

STATE = {m: {"points": [], "active": True, "green_high": None, "non_green_low": None,
             "reliability_high": None, "red_low": None, "red_classification": None,
             "msc_censored": False, "mrc_censored": False, "refinement_count": 0}
         for m in ["m1", "m2", "m3"]}
GLOBAL_LOG = []
STOP = {"triggered": False, "reason": None}


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    GLOBAL_LOG.append(line)


def point_cache_lookup(model, R):
    for p in STATE[model]["points"]:
        if p["R"] == R and p["valid"]:
            return p
    return None


def run_point(model, R, label):
    unique_label = label
    i = 2
    while (HARNESS_OUT_ROOT / f"{model}-r{R}-{unique_label}").exists() or (FINAL_OUT_ROOT / f"{model}-r{R}-{unique_label}").exists():
        unique_label = f"{label}-{i}"
        i += 1
    log(f"RUN {model} R={R} label={unique_label}")
    proc = subprocess.run([str(HARNESS), model, str(R), unique_label, str(WARMUP_SEC), str(MEASUREMENT_SEC)],
                           cwd=str(ROOT), capture_output=True, text=True, timeout=WARMUP_SEC + MEASUREMENT_SEC + 300)
    harness_dir = HARNESS_OUT_ROOT / f"{model}-r{R}-{unique_label}"
    if not harness_dir.exists():
        log(f"HARNESS FATAL: {model} R={R} rc={proc.returncode}\n{proc.stdout[-2000:]}\n{proc.stderr[-800:]}")
        return {"valid": False, "invalid_reasons": ["harness_no_output_dir"], "model": model, "target_rate": R}
    (harness_dir / "driver-harness-stdout.log").write_text(proc.stdout + "\n---STDERR---\n" + proc.stderr)
    cproc = subprocess.run([sys.executable, str(COLLECTOR), str(harness_dir)], capture_output=True, text=True)
    result_path = harness_dir / "result.json"
    if not result_path.exists():
        log(f"COLLECTOR FATAL: {model} R={R} stdout={cproc.stdout[-800:]} stderr={cproc.stderr[-800:]}")
        result = {"valid": False, "invalid_reasons": ["collector_failed"], "model": model, "target_rate": R}
    else:
        result = json.loads(result_path.read_text())
    final_dir = FINAL_OUT_ROOT / f"{model}-r{R}-{unique_label}"
    FINAL_OUT_ROOT.mkdir(parents=True, exist_ok=True)
    shutil.move(str(harness_dir), str(final_dir))
    result["_run_label"] = unique_label
    result["_final_dir"] = str(final_dir.relative_to(ROOT))
    return result


def trigger_stop(reason):
    STOP["triggered"] = True
    STOP["reason"] = reason
    log(f"*** OPEN SCREENING STOP: {reason} ***")


def record_point(model, result):
    entry = {"R": result.get("target_rate"), "label": result.get("_run_label"), "dir": result.get("_final_dir"),
              "valid": result.get("valid", False), "invalid_reasons": result.get("invalid_reasons", [])}
    mo = result.get("model_outcome", {})
    entry["color"] = mo.get("color")
    entry["classification"] = mo.get("classification")
    entry["reliability_pass"] = mo.get("reliability_pass")
    entry["slo_pass"] = mo.get("slo_pass")
    entry["throughput_sustainability_pass"] = mo.get("throughput_sustainability_pass")
    entry["backlog_sustainability_pass"] = mo.get("backlog_sustainability_pass")
    STATE[model]["points"].append(entry)
    return entry


def update_brackets(model, entry):
    if not entry["valid"]:
        return
    R = entry["R"]
    st = STATE[model]
    if entry["color"] == "GREEN":
        st["green_high"] = R if st["green_high"] is None else max(st["green_high"], R)
        st["reliability_high"] = R if st["reliability_high"] is None else max(st["reliability_high"], R)
    else:
        st["non_green_low"] = R if st["non_green_low"] is None else min(st["non_green_low"], R)
        if entry["reliability_pass"]:
            st["reliability_high"] = R if st["reliability_high"] is None else max(st["reliability_high"], R)
        if entry["color"] == "RED" and st["red_low"] is None:
            st["red_low"] = R
            st["red_classification"] = entry["classification"]


def execute_point(model, R, label):
    result = run_point(model, R, label)
    if result.get("valid"):
        record_point(model, result)
        return result, False
    reasons = set(result.get("invalid_reasons", []))
    if reasons & NO_RETRY_KEYS:
        log(f"NO-RETRY invalid {reasons & NO_RETRY_KEYS} for {model} R={R} -- STOP")
        record_point(model, result)
        trigger_stop(f"{model} R={R}: no-retry INVALID ({reasons & NO_RETRY_KEYS})")
        return result, True
    log(f"INVALID (recoverable) for {model} R={R}: {reasons} -- one retry")
    retry = run_point(model, R, f"{label}-retry1")
    if retry.get("valid"):
        record_point(model, retry)
        return retry, False
    log(f"Retry ALSO invalid for {model} R={R} -- STOP")
    record_point(model, retry)
    trigger_stop(f"{model} R={R}: original AND retry both INVALID")
    return retry, True


def resume_from_existing():
    if not FINAL_OUT_ROOT.exists():
        return
    for d in sorted(FINAL_OUT_ROOT.iterdir()):
        if not d.is_dir() or "-r" not in d.name or "-screen1" not in d.name:
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
        result["_run_label"] = d.name.split(f"{model}-r{result.get('target_rate')}-", 1)[-1]
        result["_final_dir"] = str(d.relative_to(ROOT))
        entry = record_point(model, result)
        update_brackets(model, entry)
        log(f"RESUMED {model} R={entry['R']} from existing valid artifact ({d.name}): color={entry.get('color')}")
    for model in STATE:
        st = STATE[model]
        if st["red_low"] is not None and st["red_classification"] in KNOWN_RED:
            st["active"] = False
        if any(p["R"] == SAFE_OPEN_MAX and p["valid"] and p["color"] in ("GREEN", "AMBER") for p in st["points"]):
            st["active"] = False


def initial_pass():
    for R, order in INITIAL_LEVELS:
        for model in order:
            if STOP["triggered"]:
                return
            st = STATE[model]
            if not st["active"]:
                log(f"SKIP {model} R={R}: model already inactive (known-RED or censored)")
                continue
            cached = point_cache_lookup(model, R)
            if cached:
                entry = cached
                log(f"SKIP {model} R={R}: already resumed (color={cached.get('color')})")
            else:
                result, stopped = execute_point(model, R, "screen1")
                if stopped:
                    return
                entry = st["points"][-1]
                update_brackets(model, entry)
            if entry["valid"] and entry["color"] == "RED":
                if entry["classification"] in KNOWN_RED:
                    log(f"{model} R={R}: known RED ({entry['classification']}) -- stop escalating")
                    st["active"] = False
                elif entry["classification"] in UNEXPLAINED:
                    trigger_stop(f"{model} R={R}: unexplained failure classification="
                                 f"{entry['classification']} -- investigate before any further progression")
                    return
            if R == SAFE_OPEN_MAX and entry["valid"] and entry["color"] in ("GREEN", "AMBER"):
                st["active"] = False


def refine(model):
    st = STATE[model]
    if st["red_low"] is not None and st["red_classification"] in KNOWN_RED:
        low, high = st["green_high"], st["non_green_low"]
        while low is not None and high is not None and st["refinement_count"] < MAX_REFINEMENT_POINTS:
            width = (high - low) / low if low else 1.0
            if width <= BRACKET_WIDTH_TARGET:
                break
            mid = round((low + high) / 2)
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
            if entry["valid"] and entry["color"] == "RED" and entry["classification"] in UNEXPLAINED:
                trigger_stop(f"{model} R={mid} (refinement): unexplained failure "
                             f"{entry['classification']} -- investigate before any further progression")
                return
            if entry["valid"] and entry["color"] == "GREEN":
                low = mid
            else:
                high = mid
        st["green_high"], st["non_green_low"] = low, high
    else:
        if st["green_high"] is not None and st["green_high"] >= SAFE_OPEN_MAX:
            st["msc_censored"] = True
            st["mrc_censored"] = True
            log(f"{model}: MSAR-equivalent brackets >= {SAFE_OPEN_MAX}, CONTROL_CENSORED")


def main():
    FINAL_OUT_ROOT.mkdir(parents=True, exist_ok=True)
    log("=== Phase 4 Open Screening: START ===")
    resume_from_existing()
    initial_pass()
    for model in ["m1", "m2", "m3"]:
        if STOP["triggered"]:
            break
        refine(model)
    summary = {"screening_epoch": SCREENING_EPOCH, "stop_triggered": STOP["triggered"],
               "stop_reason": STOP["reason"], "models": {}}
    for model in ["m1", "m2", "m3"]:
        st = STATE[model]
        summary["models"][model] = {
            "points": st["points"], "green_high": st["green_high"], "non_green_low": st["non_green_low"],
            "reliability_high": st["reliability_high"], "red_low": st["red_low"],
            "red_classification": st["red_classification"], "msc_censored": st["msc_censored"],
            "mrc_censored": st["mrc_censored"], "refinement_count": st["refinement_count"],
        }
    (FINAL_OUT_ROOT / "screening-summary.json").write_text(json.dumps(summary, indent=2))
    (FINAL_OUT_ROOT / "driver-log.txt").write_text("\n".join(GLOBAL_LOG))
    log("=== Phase 4 Open Screening: DONE ===")
    print(json.dumps({"stop_triggered": STOP["triggered"], "stop_reason": STOP["reason"]}, indent=2))


if __name__ == "__main__":
    main()
