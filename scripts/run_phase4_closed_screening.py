#!/usr/bin/env python3
"""Phase 4 Unit 5 — Closed-model adaptive scalability screening driver.

Orchestrates the FROZEN harness (run-phase4-closed-benchmark.sh + collect_phase4_closed_result.py
+ check_phase4_stall.py + 04-phase4-closed-wave.js + prometheus-phase4.yml) — never modifies any of
them. Implements: interleaved initial geometric levels (50/100/200/400/640, capped at
SAFE_CLOSED_MAX=640), VALID/INVALID gate before any GREEN/AMBER/RED reading, known-RED vs
unexplained-failure distinction, one-retry-then-STOP invalid policy, MSC/MRC bracket tracking,
midpoint refinement (<=20% relative width or 4 extra points), non-monotonic detection, and
control-censored bookkeeping. See docs/test-plan/phase4-design.md and the approved Unit 5 plan for
full rationale -- this docstring is deliberately terse.
"""
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
FINAL_OUT_ROOT = ROOT / "docs" / "test-results" / "phase4" / "unit5-closed-screening"
SAFE_CLOSED_MAX = 640

INITIAL_LEVELS = [
    (50, ["m1", "m2", "m3"]),
    (100, ["m2", "m3", "m1"]),
    (200, ["m3", "m1", "m2"]),
    (400, ["m1", "m2", "m3"]),
    (640, ["m2", "m3", "m1"]),
]

KNOWN_RED = {"MODEL_REJECTION", "MODEL_TIMEOUT"}
UNEXPLAINED = {"UPSTREAM_ERROR_UNCONFIRMED_CAUSE", "UNEXPECTED_CLIENT_DISCONNECT",
               "UNCLASSIFIED_MODEL_FAILURE", "INTERNAL_ERROR_BUG"}
COLOR_RANK = {"RED": 0, "AMBER": 1, "GREEN": 2}
MAX_REFINEMENT_POINTS = 4
BRACKET_WIDTH_TARGET = 0.20

STATE = {m: {
    "points": [],            # [{N, label, valid, invalid_reasons, color, classification, reliability_pass, slo_pass}]
    "active": True,          # still escalating initial geometric levels
    "green_high": None,
    "non_green_low": None,
    "reliability_high": None,
    "red_low": None,
    "red_classification": None,
    "msc_censored": False,
    "mrc_censored": False,
    "non_monotonic": False,
    "msc_refinement_count": 0,
    "mrc_refinement_count": 0,
} for m in ["m1", "m2", "m3"]}

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
    """Runs the frozen harness once, collects, relocates artifacts under unit5-closed-screening/,
    returns the parsed result dict (or an INVALID sentinel dict if harness/collector itself failed
    outright, e.g. produced no result.json)."""
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
                "run_label": unique_label, "harness_stdout_tail": proc.stdout[-3000:]}
    (harness_dir / "driver-harness-stdout.log").write_text(proc.stdout + "\n---STDERR---\n" + proc.stderr)
    cproc = subprocess.run([sys.executable, str(COLLECTOR), str(harness_dir)], capture_output=True, text=True)
    result_path = harness_dir / "result.json"
    if not result_path.exists():
        log(f"COLLECTOR FATAL: {model} N={N} stdout={cproc.stdout[-1000:]} stderr={cproc.stderr[-1000:]}")
        result = {"valid": False, "invalid_reasons": ["collector_failed"], "model": model, "concurrency": N, "run_label": unique_label}
    else:
        result = json.loads(result_path.read_text())
    # Relocate to the Unit 5-specific evidence root (pure filesystem move -- does not touch the
    # frozen harness script's own logic/semantics).
    final_dir = FINAL_OUT_ROOT / f"{model}-n{N}-{unique_label}"
    FINAL_OUT_ROOT.mkdir(parents=True, exist_ok=True)
    shutil.move(str(harness_dir), str(final_dir))
    result["_run_label"] = unique_label
    result["_final_dir"] = str(final_dir.relative_to(ROOT))
    return result


# Unit 5.3 renamed "m3_pending_no_anomaly" (any nonzero pending sample anywhere in the whole
# process-lifetime window) to "m3_connection_limit_binding_absent" (wave-scoped, time-aligned
# pending+cap evidence). Unit 5.3.1 closure added "m3_connection_metrics_evaluable" as a SEPARATE
# no-retry reason: a missing/malformed connection-pool safety signal (missing series, unexpected
# series cardinality, pool-identity mismatch, missing/invalid configured cap) must never be read as
# "no binding" -- see docs/test-results/phase4/unit5.3.1-connection-limit-temporal-coupling/SUMMARY.md.
# Both stay no-retry: a confirmed connection-limit binding is a CONTROL/CONFIGURATION LIMIT (frozen
# WEBCLIENT_MAX_CONNECTIONS constraining the primary workload, not a model boundary), and an
# unevaluable safety signal is not something to wait out by re-running the same load and hoping the
# metric appears.
NO_RETRY_KEYS = {"environment_stall_false", "pid_match", "internal_error_eq_0", "write_overflow_eq_0",
                  "m3_connection_limit_binding_absent", "m3_connection_metrics_evaluable",
                  "postflight_clean", "control_range_ok", "prewarm_contamination_ok"}

REACTOR_CONNECTION_LIMIT_KEY = "m3_connection_limit_binding_absent"
REACTOR_CONNECTION_UNEVALUABLE_KEY = "m3_connection_metrics_evaluable"


def execute_point(model, N, label):
    """Runs a point with the INVALID retry policy. Returns (result, stop_triggered)."""
    result = run_point(model, N, label)
    if result.get("valid"):
        record_point(model, result)
        return result, False

    reasons = set(result.get("invalid_reasons", []))
    if reasons & NO_RETRY_KEYS:
        if REACTOR_CONNECTION_LIMIT_KEY in reasons:
            log(f"REACTOR_CONNECTION_LIMIT for {model} N={N}: configured WEBCLIENT_MAX_CONNECTIONS "
                f"ceiling reached with sustained, time-aligned pending -- CONTROL/CONFIGURATION LIMIT, "
                f"not a model boundary. Unit 5 STOP (no automatic maxConnections increase).")
        elif REACTOR_CONNECTION_UNEVALUABLE_KEY in reasons:
            log(f"REACTOR_CONNECTION_METRICS_UNEVALUABLE for {model} N={N}: connection-pool safety "
                f"signal (series/identity/configured-cap) could not be evaluated -- Unit 5 STOP "
                f"(not treated as 'no binding').")
        else:
            log(f"NO-RETRY invalid reason(s) {reasons & NO_RETRY_KEYS} for {model} N={N} -- Unit 5 STOP")
        record_point(model, result)
        trigger_stop(f"{model} N={N}: no-retry INVALID ({reasons & NO_RETRY_KEYS}) -- environment/safety/harness-consistency violation")
        return result, True

    log(f"INVALID (recoverable class) for {model} N={N}: {reasons} -- one retry allowed")
    retry = run_point(model, N, f"{label}-retry1")
    if retry.get("valid"):
        record_point(model, retry)
        log(f"Retry succeeded for {model} N={N}")
        return retry, False

    log(f"Retry ALSO invalid for {model} N={N}: {retry.get('invalid_reasons')} -- Unit 5 STOP")
    record_point(model, retry)
    trigger_stop(f"{model} N={N}: original AND retry both INVALID -- LOADGEN/HARNESS/ENVIRONMENT blocker")
    return retry, True


def record_point(model, result):
    entry = {
        "N": result.get("concurrency"), "label": result.get("_run_label"), "dir": result.get("_final_dir"),
        "valid": result.get("valid", False), "invalid_reasons": result.get("invalid_reasons", []),
    }
    mo = result.get("model_outcome", {})
    entry["color"] = mo.get("color")
    entry["classification"] = mo.get("classification")
    entry["reliability_pass"] = mo.get("reliability_pass")
    entry["slo_pass"] = mo.get("slo_pass")
    entry["server_outcomes_wave_estimate"] = mo.get("server_outcomes_wave_estimate")
    STATE[model]["points"].append(entry)
    return entry


def trigger_stop(reason):
    STOP["triggered"] = True
    STOP["reason"] = reason
    log(f"*** UNIT 5 STOP TRIGGERED: {reason} ***")


def check_non_monotonic(model):
    pts = [p for p in STATE[model]["points"] if p["valid"]]
    pts_sorted = sorted(pts, key=lambda p: p["N"])
    for i in range(len(pts_sorted)):
        for j in range(i + 1, len(pts_sorted)):
            lo, hi = pts_sorted[i], pts_sorted[j]
            if lo["N"] == hi["N"]:
                continue
            lo_rank = COLOR_RANK.get(lo["color"], -1)
            hi_rank = COLOR_RANK.get(hi["color"], -1)
            if lo_rank < hi_rank:  # lower N strictly WORSE than higher N (i.e. result improves as
                                    # load increases) -- non-monotonic. NOTE: lo_rank > hi_rank
                                    # (e.g. N=50 GREEN, N=100 AMBER) is NORMAL monotonic
                                    # degradation, not an anomaly -- an earlier version of this
                                    # check had this comparison inverted and incorrectly flagged
                                    # ordinary degradation as non-monotonic (caught before it could
                                    # corrupt Unit 5 screening results, see driver-log.txt history).
                if not STATE[model]["non_monotonic"]:
                    log(f"{model}: SCREENING_NON_MONOTONIC detected (N={lo['N']} color={lo['color']} vs N={hi['N']} color={hi['color']})")
                STATE[model]["non_monotonic"] = True
                return True
    return False


def update_brackets(model, entry):
    if not entry["valid"]:
        return
    N = entry["N"]
    if entry["color"] == "GREEN":
        st = STATE[model]
        st["green_high"] = N if st["green_high"] is None else max(st["green_high"], N)
        st["reliability_high"] = N if st["reliability_high"] is None else max(st["reliability_high"], N)
    else:
        st = STATE[model]
        st["non_green_low"] = N if st["non_green_low"] is None else min(st["non_green_low"], N)
        if entry["reliability_pass"]:
            st["reliability_high"] = N if st["reliability_high"] is None else max(st["reliability_high"], N)
        if entry["color"] == "RED":
            if st["red_low"] is None:
                st["red_low"] = N
                st["red_classification"] = entry["classification"]


def initial_geometric_pass():
    for N, order in INITIAL_LEVELS:
        for model in order:
            if STOP["triggered"]:
                return
            st = STATE[model]
            if not st["active"]:
                log(f"SKIP {model} N={N}: model already inactive (known-RED boundary or censored)")
                continue
            cached = point_cache_lookup(model, N)
            if cached:
                log(f"SKIP {model} N={N}: already resumed from existing valid artifact (color={cached.get('color')})")
                entry = cached
            else:
                result, stopped = execute_point(model, N, "screen1")
                if stopped:
                    return
                entry = st["points"][-1]
                update_brackets(model, entry)
                check_non_monotonic(model)

            if entry["valid"] and entry["color"] == "RED":
                if entry["classification"] in KNOWN_RED:
                    log(f"{model} N={N}: known RED ({entry['classification']}) -- stop escalating this model, switch to refinement")
                    st["active"] = False
                elif entry["classification"] in UNEXPLAINED:
                    trigger_stop(f"{model} N={N}: unexplained failure classification={entry['classification']} -- investigate before any further progression")
                    return
            if N == SAFE_CLOSED_MAX and entry["valid"] and entry["color"] in ("GREEN", "AMBER"):
                st["active"] = False  # nothing higher to try; ready for censored bookkeeping / MSC-only refinement


def handle_n50_non_green_fallback():
    """Section 19: if N=50 itself was non-GREEN, don't reuse Unit 4's N=20 smoke (different
    harness generation) -- try fresh N=25, then fresh N=20 under THIS Unit's frozen harness."""
    if STOP["triggered"]:
        return
    for model in ["m1", "m2", "m3"]:
        st = STATE[model]
        p50 = next((p for p in st["points"] if p["N"] == 50 and p["valid"]), None)
        if p50 is None or p50["color"] == "GREEN":
            continue
        log(f"{model}: N=50 was {p50['color']} -- fresh lower-point screening per section 19 (N=25, then N=20)")
        for N in [25, 20]:
            if STOP["triggered"]:
                return
            result, stopped = execute_point(model, N, "screen1-lowpoint")
            if stopped:
                return
            entry = st["points"][-1]
            update_brackets(model, entry)
            check_non_monotonic(model)
            if entry["valid"] and entry["color"] == "GREEN":
                log(f"{model}: N={N} is GREEN -- lower bound established, stop lowering further")
                break
            if entry["valid"] and entry["color"] == "RED" and entry["classification"] in UNEXPLAINED:
                trigger_stop(f"{model} N={N} (lowpoint fallback): unexplained failure {entry['classification']}")
                return
        else:
            log(f"{model}: still non-GREEN at N=20 -- MSC <20 or bracket unresolved, reporting for user judgment, not auto-lowering further")


def refine_bracket(model, low, high, is_msc):
    st = STATE[model]
    count_key = "msc_refinement_count" if is_msc else "mrc_refinement_count"
    label_prefix = "msc-refine" if is_msc else "mrc-refine"
    while True:
        if STOP["triggered"] or st["non_monotonic"]:
            return low, high
        if low is None or high is None or low <= 0:
            return low, high
        width = (high - low) / low
        if width <= BRACKET_WIDTH_TARGET:
            return low, high
        if st[count_key] >= MAX_REFINEMENT_POINTS:
            log(f"{model} {'MSC' if is_msc else 'MRC'} refinement: reached {MAX_REFINEMENT_POINTS}-point cap, width={width:.1%} > 20%, stopping")
            return low, high
        mid = (low + high) // 2
        if mid == low or mid == high:
            return low, high

        cached = point_cache_lookup(model, mid)
        if cached:
            log(f"{model} {'MSC' if is_msc else 'MRC'} refinement: reusing existing valid point N={mid}")
            entry = cached
        else:
            result, stopped = execute_point(model, mid, f"{label_prefix}{st[count_key] + 1}")
            if stopped:
                return low, high
            entry = st["points"][-1]
            update_brackets(model, entry)
            if check_non_monotonic(model):
                return low, high
            st[count_key] += 1

        if not entry["valid"]:
            log(f"{model} refinement point N={mid} invalid after retry policy already applied inside execute_point; stopping this bracket's refinement")
            return low, high

        if is_msc:
            if entry["color"] == "GREEN":
                low = mid
            else:
                high = mid
        else:
            if entry["reliability_pass"]:
                low = mid
            else:
                high = mid


def refine_all():
    for model in ["m1", "m2", "m3"]:
        if STOP["triggered"]:
            break
        st = STATE[model]
        if st["non_monotonic"]:
            log(f"{model}: skipping refinement -- SCREENING_NON_MONOTONIC already flagged")
            continue

        # MSC bracket
        if st["green_high"] == SAFE_CLOSED_MAX and st["non_green_low"] is None:
            st["msc_censored"] = True
            log(f"{model}: MSC >= {SAFE_CLOSED_MAX}, CONTROL_CENSORED")
        elif st["green_high"] is not None and st["non_green_low"] is not None:
            log(f"{model}: MSC refinement starting, bracket=[{st['green_high']}, {st['non_green_low']}]")
            low, high = refine_bracket(model, st["green_high"], st["non_green_low"], is_msc=True)
            st["green_high"], st["non_green_low"] = low, high
        elif st["green_high"] is None:
            log(f"{model}: no GREEN point found even after low-point fallback -- MSC bracket unresolved, reporting as-is")

        if STOP["triggered"]:
            break

        # MRC bracket
        if st["red_low"] is None:
            if st["reliability_high"] == SAFE_CLOSED_MAX:
                st["mrc_censored"] = True
                log(f"{model}: MRC >= {SAFE_CLOSED_MAX}, CONTROL_CENSORED")
            else:
                log(f"{model}: no RED found and reliability_high={st['reliability_high']} < {SAFE_CLOSED_MAX} -- MRC bracket incomplete (model became inactive before reaching censorship or a RED boundary)")
        else:
            log(f"{model}: MRC refinement starting, bracket=[{st['reliability_high']}, {st['red_low']}]")
            low, high = refine_bracket(model, st["reliability_high"], st["red_low"], is_msc=False)
            st["reliability_high"], st["red_low"] = low, high


def resume_from_existing():
    """Picks up already-completed VALID '<model>-n<N>-screen1' points from a prior (aborted) driver
    invocation instead of re-running them -- Unit 5 screening points are not Formal repeats, so a
    valid point already on disk is reused verbatim (docs/test-plan brief section 23/24)."""
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
        check_non_monotonic(model)
        # Re-derive 'active' flag from resumed points: inactive if a known-RED boundary was
        # already found, or if N=640 was already reached with GREEN/AMBER (nothing higher to try).
        st = STATE[model]
        if st["red_low"] is not None and st["red_classification"] in KNOWN_RED:
            st["active"] = False
        if any(p["N"] == SAFE_CLOSED_MAX and p["valid"] and p["color"] in ("GREEN", "AMBER") for p in st["points"]):
            st["active"] = False


def main():
    FINAL_OUT_ROOT.mkdir(parents=True, exist_ok=True)
    log("=== Phase 4 Unit 5 Closed Screening: START ===")
    resume_from_existing()
    initial_geometric_pass()
    if not STOP["triggered"]:
        handle_n50_non_green_fallback()
    if not STOP["triggered"]:
        refine_all()

    summary = {
        "safe_closed_max": SAFE_CLOSED_MAX,
        "stop_triggered": STOP["triggered"],
        "stop_reason": STOP["reason"],
        "models": {},
    }
    for model in ["m1", "m2", "m3"]:
        st = STATE[model]
        summary["models"][model] = {
            "points": st["points"],
            "green_high": st["green_high"], "non_green_low": st["non_green_low"],
            "reliability_high": st["reliability_high"], "red_low": st["red_low"],
            "red_classification": st["red_classification"],
            "msc_bracket": [st["green_high"], st["non_green_low"]],
            "mrc_bracket": [st["reliability_high"], st["red_low"]],
            "msc_censored": st["msc_censored"], "mrc_censored": st["mrc_censored"],
            "non_monotonic": st["non_monotonic"],
            "msc_refinement_count": st["msc_refinement_count"], "mrc_refinement_count": st["mrc_refinement_count"],
        }
    (FINAL_OUT_ROOT / "screening-summary.json").write_text(json.dumps(summary, indent=2))
    (FINAL_OUT_ROOT / "driver-log.txt").write_text("\n".join(GLOBAL_LOG))
    log("=== Phase 4 Unit 5 Closed Screening: DONE ===")
    print(json.dumps({"stop_triggered": STOP["triggered"], "stop_reason": STOP["reason"]}, indent=2))


if __name__ == "__main__":
    main()
