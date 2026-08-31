#!/usr/bin/env python3
"""Synthetic tests for run_phase4_open_screening.py's STOP/classification logic. No real
Gateway/Mock/k6 process, no subprocess call at all -- run_point is monkeypatched.

Direct motivation (docs/test-results/phase4/unit7.1-m2-r160-frontdoor-forensics/
driver-stop-regression.md): the driver had NO check at all for "unexplained failure"
classifications (UNCLASSIFIED_MODEL_FAILURE, etc.) -- only KNOWN_RED deactivated a model, and
nothing halted the driver. In the actual M2 R=160 incident, this let the driver proceed straight
into M3 R=160 immediately after M2's UNCLASSIFIED_MODEL_FAILURE, before a human had a chance to
investigate. These tests pin the fix: any UNEXPLAINED classification must halt the ENTIRE driver,
with zero further runs of ANY model at that rate level or beyond, whereas a KNOWN_RED classification
must only deactivate that one model and let the others continue.
"""
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


def fresh_driver():
    if "run_phase4_open_screening" in sys.modules:
        del sys.modules["run_phase4_open_screening"]
    return importlib.import_module("run_phase4_open_screening")


def make_result(model, R, color, classification, reliability_pass, valid=True):
    return {
        "valid": valid, "invalid_reasons": [], "model": model, "target_rate": R,
        "_run_label": "screen1", "_final_dir": f"synthetic/{model}-r{R}-screen1",
        "model_outcome": {
            "color": color, "classification": classification,
            "reliability_pass": reliability_pass, "slo_pass": (color == "GREEN"),
            "throughput_sustainability_pass": (color == "GREEN"),
            "backlog_sustainability_pass": True,
        },
    }


# ---- The real incident: M2 UNCLASSIFIED_MODEL_FAILURE must stop the whole driver, including M3 ----
def test_unexplained_stops_entire_driver_and_blocks_next_model():
    d = fresh_driver()
    calls = []

    def fake_run_point(model, R, label):
        calls.append((model, R))
        if model == "m1":
            return make_result(model, R, "GREEN", "GREEN", True)
        if model == "m2" and R == 160:
            return make_result(model, R, "RED", "UNCLASSIFIED_MODEL_FAILURE", False)
        return make_result(model, R, "GREEN", "GREEN", True)

    d.run_point = fake_run_point
    # INITIAL_LEVELS already includes (160, ["m1", "m2", "m3"]) after lower levels -- run the real
    # driver loop end-to-end exactly as main() would, just with run_point faked out.
    d.initial_pass()

    check("STOP triggered", d.STOP["triggered"] is True)
    check("stop_reason mentions the classification", "UNCLASSIFIED_MODEL_FAILURE" in (d.STOP["reason"] or ""))
    m3_r160_calls = [c for c in calls if c == ("m3", 160)]
    check("m3 R=160 was NEVER run after m2 R=160's unexplained failure", len(m3_r160_calls) == 0)
    m1_r256_calls = [c for c in calls if c == ("m1", 256)]
    check("m1 R=256 (a later level) was NEVER run either -- whole driver halted, not just current level",
          len(m1_r256_calls) == 0)


# ---- KNOWN_RED must only deactivate the one model; others keep progressing ----
def test_known_red_only_deactivates_that_model():
    d = fresh_driver()
    calls = []

    def fake_run_point(model, R, label):
        calls.append((model, R))
        if model == "m1" and R >= 10:
            return make_result(model, R, "RED", "MODEL_TIMEOUT", False)
        return make_result(model, R, "GREEN", "GREEN", True)

    d.run_point = fake_run_point
    d.initial_pass()

    check("STOP not triggered for known RED", d.STOP["triggered"] is False)
    check("m1 deactivated after known RED", d.STATE["m1"]["active"] is False)
    m1_r20_calls = [c for c in calls if c == ("m1", 20)]
    check("m1 R=20 never run after m1 became inactive at R=10", len(m1_r20_calls) == 0)
    m2_r256_calls = [c for c in calls if c == ("m2", 256)]
    check("m2 kept progressing all the way to R=256 despite m1's known RED",
          len(m2_r256_calls) == 1)
    m3_r256_calls = [c for c in calls if c == ("m3", 256)]
    check("m3 also kept progressing to R=256", len(m3_r256_calls) == 1)


# ---- UNEXPECTED_CLIENT_DISCONNECT (a different UNEXPLAINED classification) also stops everything ----
def test_unexpected_client_disconnect_also_stops():
    d = fresh_driver()
    calls = []

    def fake_run_point(model, R, label):
        calls.append((model, R))
        if model == "m3" and R == 40:
            return make_result(model, R, "RED", "UNEXPECTED_CLIENT_DISCONNECT", False)
        return make_result(model, R, "GREEN", "GREEN", True)

    d.run_point = fake_run_point
    d.initial_pass()

    check("STOP triggered for UNEXPECTED_CLIENT_DISCONNECT", d.STOP["triggered"] is True)
    later_calls = [c for c in calls if c[1] > 40]
    check("nothing at a higher rate than the stopping point was ever run", len(later_calls) == 0)


# ---- Environment/control INVALID (no-retry) halts the whole driver too, distinct from UNEXPLAINED ----
def test_no_retry_invalid_halts_driver():
    d = fresh_driver()
    calls = []

    def fake_run_point(model, R, label):
        calls.append((model, R))
        if model == "m2" and R == 20:
            return {"valid": False, "invalid_reasons": ["clock_integrity_ok"], "model": model,
                    "target_rate": R, "_run_label": label}
        return make_result(model, R, "GREEN", "GREEN", True)

    d.run_point = fake_run_point
    d.initial_pass()

    check("STOP triggered for no-retry INVALID (clock_integrity_ok)", d.STOP["triggered"] is True)
    m3_r20_calls = [c for c in calls if c == ("m3", 20)]
    check("m3 R=20 never run after m2's no-retry INVALID at the same level", len(m3_r20_calls) == 0)


# ---- Refinement phase must also honor the UNEXPLAINED stop (gap that exists even in the Closed
# driver's own refine_bracket precedent -- closed here for the Open driver deliberately) ----
def test_unexplained_during_refinement_also_stops():
    d = fresh_driver()
    d.STATE["m2"]["active"] = False
    d.STATE["m2"]["green_high"] = 100
    d.STATE["m2"]["non_green_low"] = 200
    d.STATE["m2"]["red_low"] = 200
    d.STATE["m2"]["red_classification"] = "MODEL_TIMEOUT"

    def fake_run_point(model, R, label):
        return make_result(model, R, "RED", "UNCLASSIFIED_MODEL_FAILURE", False)

    d.run_point = fake_run_point
    d.refine("m2")
    check("STOP triggered during refinement on unexplained failure", d.STOP["triggered"] is True)


def main():
    test_unexplained_stops_entire_driver_and_blocks_next_model()
    test_known_red_only_deactivates_that_model()
    test_unexpected_client_disconnect_also_stops()
    test_no_retry_invalid_halts_driver()
    test_unexplained_during_refinement_also_stops()
    print()
    if failures:
        print(f"{len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
