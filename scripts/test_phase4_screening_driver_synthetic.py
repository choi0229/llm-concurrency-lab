#!/usr/bin/env python3
"""Synthetic tests for run_phase4_closed_screening.py's orchestration/classification logic (Unit
5.1 brief section 18) -- no real Gateway/Mock/k6 process, no subprocess call at all. Covers the
exact scenario list requested, including the non-monotonic-comparison-inversion bug found and
fixed mid-Unit-5 (the direct motivation for this test file existing at all).
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
    """Re-imports the driver module fresh so its module-level STATE/STOP globals reset between
    scenarios (the driver was written as a single-shot script, not a reusable library -- this is
    the simplest way to test it without refactoring it into a class for testability alone)."""
    if "run_phase4_closed_screening" in sys.modules:
        del sys.modules["run_phase4_closed_screening"]
    return importlib.import_module("run_phase4_closed_screening")


def make_entry(N, color, reliability_pass, classification=None, valid=True):
    return {
        "N": N, "label": "synthetic", "dir": "synthetic", "valid": valid, "invalid_reasons": [],
        "color": color, "classification": classification or color,
        "reliability_pass": reliability_pass, "slo_pass": (color == "GREEN"),
        "server_outcomes_wave_estimate": {},
    }


# ---- Non-monotonic detection (the actual bug this Unit fixed) ----
def test_monotonic_cases():
    d = fresh_driver()

    # GREEN(50) -> GREEN(100): monotonic (no change), not flagged.
    d.STATE["m1"]["points"] = [make_entry(50, "GREEN", True), make_entry(100, "GREEN", True)]
    check("GREEN->GREEN is monotonic (not flagged)", d.check_non_monotonic("m1") is False)

    # GREEN(50) -> AMBER(100): NORMAL degradation as load increases -- must NOT be flagged.
    # This is the exact case the original buggy comparison incorrectly flagged.
    d = fresh_driver()
    d.STATE["m1"]["points"] = [make_entry(50, "GREEN", True), make_entry(100, "AMBER", True)]
    check("GREEN(50)->AMBER(100) is NORMAL monotonic degradation (must NOT be flagged)",
          d.check_non_monotonic("m1") is False)

    # GREEN(50) -> RED(100): normal degradation, not flagged.
    d = fresh_driver()
    d.STATE["m1"]["points"] = [make_entry(50, "GREEN", True), make_entry(100, "RED", False)]
    check("GREEN(50)->RED(100) is normal monotonic degradation (not flagged)",
          d.check_non_monotonic("m1") is False)

    # AMBER(50) -> RED(100): normal degradation, not flagged.
    d = fresh_driver()
    d.STATE["m1"]["points"] = [make_entry(50, "AMBER", True), make_entry(100, "RED", False)]
    check("AMBER(50)->RED(100) is normal monotonic degradation (not flagged)",
          d.check_non_monotonic("m1") is False)

    # AMBER(50) -> GREEN(100): result IMPROVES as load increases -- genuinely non-monotonic.
    d = fresh_driver()
    d.STATE["m1"]["points"] = [make_entry(50, "AMBER", True), make_entry(100, "GREEN", True)]
    check("AMBER(50)->GREEN(100) IS non-monotonic (result improved with more load)",
          d.check_non_monotonic("m1") is True)

    # RED(100) -> GREEN(200): result improves as load increases -- genuinely non-monotonic.
    d = fresh_driver()
    d.STATE["m1"]["points"] = [make_entry(100, "RED", False), make_entry(200, "GREEN", True)]
    check("RED(100)->GREEN(200) IS non-monotonic", d.check_non_monotonic("m1") is True)


# ---- Bracket computation ----
def test_bracket_updates():
    d = fresh_driver()
    for e in [make_entry(50, "GREEN", True), make_entry(100, "AMBER", True), make_entry(200, "RED", False, "MODEL_TIMEOUT")]:
        d.STATE["m1"]["points"].append(e)
        d.update_brackets("m1", e)
    st = d.STATE["m1"]
    check("green_high == 50", st["green_high"] == 50)
    check("non_green_low == 100", st["non_green_low"] == 100)
    check("reliability_high == 100 (AMBER still counts as reliability pass)", st["reliability_high"] == 100)
    check("red_low == 200", st["red_low"] == 200)
    check("red_classification == MODEL_TIMEOUT", st["red_classification"] == "MODEL_TIMEOUT")


# ---- Control-censored: N=640 GREEN -> both censored ----
def test_control_censored_640_green():
    d = fresh_driver()
    e = make_entry(640, "GREEN", True)
    d.STATE["m1"]["points"].append(e)
    d.update_brackets("m1", e)
    d.STATE["m1"]["green_high"] = 640
    d.refine_all()
    st = d.STATE["m1"]
    check("640 GREEN -> msc_censored True", st["msc_censored"] is True)
    check("640 GREEN -> mrc_censored True (no RED found, reliability_high==640)", st["mrc_censored"] is True)


# ---- Control-censored: N=640 AMBER -> MRC censored, MSC bracket still refinable ----
def test_control_censored_640_amber():
    d = fresh_driver()
    for e in [make_entry(400, "GREEN", True), make_entry(640, "AMBER", True)]:
        d.STATE["m1"]["points"].append(e)
        d.update_brackets("m1", e)
    d.refine_bracket = lambda model, low, high, is_msc: (low, high)  # no-op: don't fetch new points
    d.refine_all()
    st = d.STATE["m1"]
    check("640 AMBER -> mrc_censored True (reliability_high==640, no RED)", st["mrc_censored"] is True)
    check("640 AMBER -> msc_censored False (non_green_low=640 exists, refinable)", st["msc_censored"] is False)


# ---- Known RED stops escalation for that model only ----
def test_known_red_stops_model():
    d = fresh_driver()
    e = make_entry(200, "RED", False, "MODEL_REJECTION")
    d.STATE["m1"]["points"].append(e)
    d.update_brackets("m1", e)
    if e["classification"] in d.KNOWN_RED:
        d.STATE["m1"]["active"] = False
    check("MODEL_REJECTION -> model becomes inactive", d.STATE["m1"]["active"] is False)
    check("STOP not triggered for known RED", d.STOP["triggered"] is False)


# ---- Unexplained failure triggers full-driver STOP ----
def test_unexplained_triggers_stop():
    d = fresh_driver()
    e = make_entry(100, "RED", False, "UNCLASSIFIED_MODEL_FAILURE")
    if e["classification"] in d.UNEXPLAINED:
        d.trigger_stop(f"m2 N=100: unexplained failure classification={e['classification']}")
    check("UNCLASSIFIED_MODEL_FAILURE -> STOP triggered", d.STOP["triggered"] is True)
    check("stop_reason mentions the classification", "UNCLASSIFIED_MODEL_FAILURE" in d.STOP["reason"])


# ---- INVALID retry policy: one retry allowed for recoverable reasons ----
def test_invalid_one_retry_then_success():
    d = fresh_driver()
    calls = {"n": 0}

    def fake_run_point(model, N, label):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"valid": False, "invalid_reasons": ["k6_exit_zero"], "model": model, "concurrency": N, "_run_label": label}
        return {"valid": True, "model": model, "concurrency": N, "_run_label": label,
                "model_outcome": {"color": "GREEN", "classification": "GREEN", "reliability_pass": True, "slo_pass": True}}

    d.run_point = fake_run_point
    result, stopped = d.execute_point("m1", 50, "screen1")
    check("recoverable invalid -> 2 calls made (1 original + 1 retry)", calls["n"] == 2)
    check("retry succeeded -> not stopped", stopped is False)
    check("retry succeeded -> valid result recorded", d.STATE["m1"]["points"][-1]["valid"] is True)


def test_repeated_invalid_stops():
    d = fresh_driver()
    calls = {"n": 0}

    def fake_run_point(model, N, label):
        calls["n"] += 1
        return {"valid": False, "invalid_reasons": ["k6_exit_zero"], "model": model, "concurrency": N, "_run_label": label}

    d.run_point = fake_run_point
    result, stopped = d.execute_point("m1", 50, "screen1")
    check("repeated invalid -> exactly 2 calls (original + 1 retry, no more)", calls["n"] == 2)
    check("repeated invalid -> STOP triggered", stopped is True and d.STOP["triggered"] is True)


def test_no_retry_class_invalid_stops_immediately():
    d = fresh_driver()
    calls = {"n": 0}

    def fake_run_point(model, N, label):
        calls["n"] += 1
        return {"valid": False, "invalid_reasons": ["environment_stall_false"], "model": model, "concurrency": N, "_run_label": label}

    d.run_point = fake_run_point
    result, stopped = d.execute_point("m1", 50, "screen1")
    check("no-retry-class invalid -> exactly 1 call (no retry attempted)", calls["n"] == 1)
    check("no-retry-class invalid -> STOP triggered immediately", stopped is True and d.STOP["triggered"] is True)


def test_reactor_connection_limit_key_is_no_retry():
    """Unit 5.3 pending-validity amendment: m3_connection_limit_binding_absent (renamed from
    m3_pending_no_anomaly) must remain in NO_RETRY_KEYS -- a confirmed connection-limit binding is
    a CONTROL/CONFIGURATION LIMIT, not a transient to retry past."""
    d = fresh_driver()
    check("m3_connection_limit_binding_absent is a NO_RETRY_KEYS member",
          "m3_connection_limit_binding_absent" in d.NO_RETRY_KEYS)
    check("the old m3_pending_no_anomaly name is gone (renamed, not aliased)",
          "m3_pending_no_anomaly" not in d.NO_RETRY_KEYS)

    calls = {"n": 0}

    def fake_run_point(model, N, label):
        calls["n"] += 1
        return {"valid": False, "invalid_reasons": ["m3_connection_limit_binding_absent"],
                "model": model, "concurrency": N, "_run_label": label}

    d.run_point = fake_run_point
    result, stopped = d.execute_point("m3", 400, "screen1")
    check("connection-limit-binding invalid -> exactly 1 call (no retry)", calls["n"] == 1)
    check("connection-limit-binding invalid -> STOP triggered immediately", stopped is True and d.STOP["triggered"] is True)


def test_reactor_connection_unevaluable_key_is_no_retry():
    """Unit 5.3.1 closure: m3_connection_metrics_evaluable is a SEPARATE no-retry reason from
    m3_connection_limit_binding_absent -- a missing/malformed connection-pool safety signal must
    stop the screening, not be silently retried in the hope the metric appears next time."""
    d = fresh_driver()
    check("m3_connection_metrics_evaluable is a NO_RETRY_KEYS member",
          "m3_connection_metrics_evaluable" in d.NO_RETRY_KEYS)

    calls = {"n": 0}

    def fake_run_point(model, N, label):
        calls["n"] += 1
        return {"valid": False, "invalid_reasons": ["m3_connection_metrics_evaluable"],
                "model": model, "concurrency": N, "_run_label": label}

    d.run_point = fake_run_point
    result, stopped = d.execute_point("m3", 400, "screen1")
    check("connection-metrics-unevaluable invalid -> exactly 1 call (no retry)", calls["n"] == 1)
    check("connection-metrics-unevaluable invalid -> STOP triggered immediately", stopped is True and d.STOP["triggered"] is True)


def main():
    test_monotonic_cases()
    test_bracket_updates()
    test_control_censored_640_green()
    test_control_censored_640_amber()
    test_known_red_stops_model()
    test_unexplained_triggers_stop()
    test_invalid_one_retry_then_success()
    test_repeated_invalid_stops()
    test_no_retry_class_invalid_stops_immediately()
    test_reactor_connection_limit_key_is_no_retry()
    test_reactor_connection_unevaluable_key_is_no_retry()

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s): {failures}")
        sys.exit(1)
    print("ALL SCREENING DRIVER SYNTHETIC TESTS PASSED")


if __name__ == "__main__":
    main()
