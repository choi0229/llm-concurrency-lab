#!/usr/bin/env python3
"""Synthetic tests for run_phase4_closed_formal.py's confirmation/invalid-retry/Canary logic
(docs/test-plan/phase4-closed-formal-protocol.md) -- no real Gateway/Mock/k6 process, no
subprocess call at all. Mirrors the style of test_phase4_screening_driver_synthetic.py.
"""
import importlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


def fresh_driver():
    if "run_phase4_closed_formal" in sys.modules:
        del sys.modules["run_phase4_closed_formal"]
    return importlib.import_module("run_phase4_closed_formal")


def mo(color=None, classification=None, reliability_pass=None, slo_pass=None):
    return {"color": color, "classification": classification, "reliability_pass": reliability_pass, "slo_pass": slo_pass}


def add_rep(d, cid, valid, model_outcome=None, invalid_reasons=None):
    """Directly exercises execute_rep's record-shape via a fake run_point, appending to STATE."""
    cell = d.FORMAL_CELLS[cid]
    if valid:
        result = {"valid": True, "model_outcome": model_outcome, "_run_label": "x", "_final_dir": "x"}
    else:
        result = {"valid": False, "invalid_reasons": invalid_reasons or [], "model_outcome": {}, "_run_label": "x"}
    calls = {"n": 0}

    def fake_run_point(model, N, label):
        calls["n"] += 1
        return result, label
    d.run_point = fake_run_point
    r, stopped = d.execute_rep(cell["model"], cell["N"], "test")
    if r.get("valid"):
        mo_ = r.get("model_outcome", {}) or {}
        d.STATE[cid]["reps"].append({
            "label": r.get("_run_label"), "dir": r.get("_final_dir"), "valid": True,
            "color": mo_.get("color"), "classification": mo_.get("classification"),
            "reliability_pass": mo_.get("reliability_pass"), "slo_pass": mo_.get("slo_pass"),
            "boundary_pass": bool(cell["boundary"](mo_)), "signature_pass": bool(cell["signature"](mo_)),
        })
    return stopped


def test_confirmed_sustainable_f1():
    d = fresh_driver()
    for _ in range(3):
        add_rep(d, "F1", True, mo(color="GREEN"))
    state = d.confirm_cell("F1")
    check("F1 3/3 GREEN -> CONFIRMED SUSTAINABLE", state == "CONFIRMED SUSTAINABLE")


def test_confirmed_boundary_f2():
    d = fresh_driver()
    for _ in range(3):
        add_rep(d, "F2", True, mo(color="AMBER", reliability_pass=True, slo_pass=False))
    state = d.confirm_cell("F2")
    check("F2 3/3 non-GREEN -> CONFIRMED (not 'SUSTAINABLE' wording)", state == "CONFIRMED")


def test_confirmed_unstable_f1():
    d = fresh_driver()
    add_rep(d, "F1", True, mo(color="GREEN"))
    add_rep(d, "F1", True, mo(color="AMBER"))
    add_rep(d, "F1", True, mo(color="RED"))
    state = d.confirm_cell("F1")
    check("F1 1/3 GREEN (2 fail) -> CONFIRMED UNSTABLE", state == "CONFIRMED UNSTABLE")


def test_not_confirmed_f2():
    d = fresh_driver()
    add_rep(d, "F2", True, mo(color="AMBER"))
    add_rep(d, "F2", True, mo(color="GREEN"))
    add_rep(d, "F2", True, mo(color="GREEN"))
    state = d.confirm_cell("F2")
    check("F2 1/3 non-GREEN (2 fail) -> NOT CONFIRMED", state == "NOT CONFIRMED")


def test_ambiguous_then_resolved_confirmed():
    d = fresh_driver()
    add_rep(d, "F1", True, mo(color="GREEN"))
    add_rep(d, "F1", True, mo(color="GREEN"))
    add_rep(d, "F1", True, mo(color="AMBER"))
    check("F1 2/3 GREEN -> AMBIGUOUS at n=3", d.confirm_cell("F1") == "AMBIGUOUS")
    add_rep(d, "F1", True, mo(color="GREEN"))
    add_rep(d, "F1", True, mo(color="GREEN"))
    state = d.confirm_cell("F1")
    check("F1 4/5 GREEN after extras -> CONFIRMED SUSTAINABLE", state == "CONFIRMED SUSTAINABLE")


def test_ambiguous_then_inconclusive_reachability():
    """5-0 / 0-5 are mathematically unreachable from a (2 pass, 1 fail) ambiguous seed -- (3,2)
    must resolve INCONCLUSIVE RANGE, not be stuck waiting for an impossible unanimous 5."""
    d = fresh_driver()
    add_rep(d, "F1", True, mo(color="GREEN"))
    add_rep(d, "F1", True, mo(color="GREEN"))
    add_rep(d, "F1", True, mo(color="AMBER"))
    add_rep(d, "F1", True, mo(color="GREEN"))
    add_rep(d, "F1", True, mo(color="AMBER"))
    state = d.confirm_cell("F1")
    check("F1 3/5 GREEN (still mixed) after extras -> INCONCLUSIVE RANGE", state == "INCONCLUSIVE RANGE")


def test_ambiguous_reversal_confirmed_unstable():
    """Unit 5.5 closure: 2 PASS/3 FAIL at n=5 is a reachable state (2/1 seed + FAIL + FAIL) and is
    frozen as CONFIRMED UNSTABLE (unstable evidence clearly dominant), not INCONCLUSIVE RANGE."""
    d = fresh_driver()
    add_rep(d, "F1", True, mo(color="GREEN"))
    add_rep(d, "F1", True, mo(color="GREEN"))
    add_rep(d, "F1", True, mo(color="AMBER"))
    check("F1 2/1 seed -> AMBIGUOUS at n=3", d.confirm_cell("F1") == "AMBIGUOUS")
    add_rep(d, "F1", True, mo(color="AMBER"))
    add_rep(d, "F1", True, mo(color="AMBER"))
    state = d.confirm_cell("F1")
    check("F1 2/1 seed + FAIL + FAIL -> 2 PASS/3 FAIL -> CONFIRMED UNSTABLE", state == "CONFIRMED UNSTABLE")


def test_n5_three_two_is_inconclusive():
    """2/1 seed + PASS + FAIL -> 3 PASS/2 FAIL -> INCONCLUSIVE RANGE (genuinely still mixed)."""
    d = fresh_driver()
    add_rep(d, "F1", True, mo(color="GREEN"))
    add_rep(d, "F1", True, mo(color="GREEN"))
    add_rep(d, "F1", True, mo(color="AMBER"))
    add_rep(d, "F1", True, mo(color="GREEN"))
    add_rep(d, "F1", True, mo(color="AMBER"))
    state = d.confirm_cell("F1")
    check("F1 2/1 seed + PASS + FAIL -> 3 PASS/2 FAIL -> INCONCLUSIVE RANGE", state == "INCONCLUSIVE RANGE")


def test_n5_four_one_is_confirmed():
    """2/1 seed + PASS + PASS -> 4 PASS/1 FAIL -> CONFIRMED (SUSTAINABLE)."""
    d = fresh_driver()
    add_rep(d, "F1", True, mo(color="GREEN"))
    add_rep(d, "F1", True, mo(color="GREEN"))
    add_rep(d, "F1", True, mo(color="AMBER"))
    add_rep(d, "F1", True, mo(color="GREEN"))
    add_rep(d, "F1", True, mo(color="GREEN"))
    state = d.confirm_cell("F1")
    check("F1 2/1 seed + PASS + PASS -> 4 PASS/1 FAIL -> CONFIRMED SUSTAINABLE", state == "CONFIRMED SUSTAINABLE")


def test_n3_three_zero_confirmed():
    d = fresh_driver()
    for _ in range(3):
        add_rep(d, "F1", True, mo(color="GREEN"))
    check("3 PASS/0 FAIL -> CONFIRMED SUSTAINABLE", d.confirm_cell("F1") == "CONFIRMED SUSTAINABLE")


def test_n3_one_two_confirmed_unstable():
    d = fresh_driver()
    add_rep(d, "F1", True, mo(color="GREEN"))
    add_rep(d, "F1", True, mo(color="AMBER"))
    add_rep(d, "F1", True, mo(color="AMBER"))
    check("1 PASS/2 FAIL -> CONFIRMED UNSTABLE", d.confirm_cell("F1") == "CONFIRMED UNSTABLE")


def test_n3_zero_three_confirmed_unstable():
    d = fresh_driver()
    for _ in range(3):
        add_rep(d, "F1", True, mo(color="AMBER"))
    check("0 PASS/3 FAIL -> CONFIRMED UNSTABLE", d.confirm_cell("F1") == "CONFIRMED UNSTABLE")


def test_no_new_labels_used():
    """CONFIRMED_WITH_VARIABILITY and MIXED must never appear as confirm_cell outputs."""
    d = fresh_driver()
    forbidden = {"CONFIRMED_WITH_VARIABILITY", "MIXED"}
    all_states = set()
    scenarios = [
        [("GREEN",)] * 3,
        [("AMBER",)] * 3,
        [("GREEN",), ("AMBER",), ("AMBER",)],
        [("GREEN",), ("GREEN",), ("AMBER",), ("GREEN",), ("GREEN",)],
        [("GREEN",), ("GREEN",), ("AMBER",), ("GREEN",), ("AMBER",)],
        [("GREEN",), ("GREEN",), ("AMBER",), ("AMBER",), ("AMBER",)],
    ]
    for scenario in scenarios:
        d2 = fresh_driver()
        for (color,) in scenario:
            add_rep(d2, "F1", True, mo(color=color))
        all_states.add(d2.confirm_cell("F1"))
    check("no forbidden label appears across all reachable n=3/n=5 states", not (all_states & forbidden))
    check("only frozen §14 vocabulary appears", all_states <= {"CONFIRMED SUSTAINABLE", "CONFIRMED UNSTABLE", "AMBIGUOUS", "INCONCLUSIVE RANGE"})


def test_invalid_excluded_from_replicate_count():
    d = fresh_driver()
    add_rep(d, "F1", True, mo(color="GREEN"))
    add_rep(d, "F1", False, invalid_reasons=["k6_exit_zero"])  # recoverable-class marker unused here; just checking exclusion
    check("invalid rep not appended to STATE reps", len(d.STATE["F1"]["reps"]) == 1)
    check("confirm_cell with n=1 valid rep -> None (not enough)", d.confirm_cell("F1") is None)


def test_recoverable_invalid_retry_succeeds():
    d = fresh_driver()
    cell = d.FORMAL_CELLS["F1"]
    calls = {"n": 0}

    def fake_run_point(model, N, label):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"valid": False, "invalid_reasons": ["k6_exit_zero"], "model_outcome": {}, "_run_label": label}, label
        return {"valid": True, "model_outcome": mo(color="GREEN"), "_run_label": label, "_final_dir": "x"}, label

    d.run_point = fake_run_point
    r, stopped = d.execute_rep(cell["model"], cell["N"], "rep1")
    check("recoverable invalid -> exactly 2 calls (original + 1 retry)", calls["n"] == 2)
    check("recoverable invalid -> retry succeeded, not stopped", stopped is False and r.get("valid") is True)


def test_no_retry_invalid_stops_immediately():
    d = fresh_driver()
    cell = d.FORMAL_CELLS["F6"]
    calls = {"n": 0}

    def fake_run_point(model, N, label):
        calls["n"] += 1
        return {"valid": False, "invalid_reasons": ["m3_connection_limit_binding_absent"], "model_outcome": {}, "_run_label": label}, label

    d.run_point = fake_run_point
    r, stopped = d.execute_rep(cell["model"], cell["N"], "rep1")
    check("no-retry invalid -> exactly 1 call (no retry)", calls["n"] == 1)
    check("no-retry invalid -> STOP triggered immediately", stopped is True and d.STOP["triggered"] is True)


def test_canary_pass():
    d = fresh_driver()
    tmp = tempfile.mkdtemp()
    d.FINAL_OUT_ROOT = Path(tmp)  # canary writes canary-result.json; never touch the real output root in a synthetic test
    good_result = {
        "valid": True,
        "client_cohort": {"target_concurrency": 640, "actual_started": 640, "invariant_ok": True},
        "model_outcome": {"color": "GREEN"},
        "stall_check": {"stall_detected": False},
        "postflight_clean": True,
        "implementation_specific": {"reactor_connection_pool": {"connection_limit_evaluable": True, "connection_limit_binding": False}},
        "_run_label": "canary",
    }

    def fake_run_point(model, N, label):
        return good_result, label
    d.run_point = fake_run_point
    check("canary all-clean -> PASS", d.run_canary() is True)
    check("canary PASS -> STOP not triggered", d.STOP["triggered"] is False)


def test_canary_fail_stops():
    d = fresh_driver()
    tmp = tempfile.mkdtemp()
    d.FINAL_OUT_ROOT = Path(tmp)
    bad_result = {
        "valid": True,
        "client_cohort": {"target_concurrency": 640, "actual_started": 640, "invariant_ok": True},
        "model_outcome": {"color": "AMBER"},  # not GREEN -> canary fails
        "stall_check": {"stall_detected": False},
        "postflight_clean": True,
        "implementation_specific": {"reactor_connection_pool": {"connection_limit_evaluable": True, "connection_limit_binding": False}},
        "_run_label": "canary",
    }

    def fake_run_point(model, N, label):
        return bad_result, label
    d.run_point = fake_run_point
    check("canary non-GREEN -> FAIL", d.run_canary() is False)
    check("canary FAIL -> STOP triggered", d.STOP["triggered"] is True)


def test_boundary_vs_signature_separation_f2():
    """If F2 (expected AMBER) reproduces as RED instead: the MSC boundary predicate (non-GREEN)
    still holds, but the AMBER signature does not -- both facts recorded separately."""
    d = fresh_driver()
    cell = d.FORMAL_CELLS["F2"]
    outcome = mo(color="RED", classification="MODEL_TIMEOUT", reliability_pass=False)
    check("F2 boundary predicate (non-GREEN) holds even if RED", cell["boundary"](outcome) is True)
    check("F2 AMBER signature predicate fails when RED", cell["signature"](outcome) is False)


def test_boundary_vs_signature_separation_f4():
    """If F4 (expected MODEL_TIMEOUT) reproduces as a different known RED: the MRC boundary
    predicate (reliability_pass==False) still holds, but the MODEL_TIMEOUT signature does not."""
    d = fresh_driver()
    cell = d.FORMAL_CELLS["F4"]
    outcome = mo(color="RED", classification="MODEL_REJECTION", reliability_pass=False)
    check("F4 boundary predicate (reliability_pass==False) holds for any RED", cell["boundary"](outcome) is True)
    check("F4 MODEL_TIMEOUT signature predicate fails for a different RED classification", cell["signature"](outcome) is False)


def _canary_artifact(tmp: Path, pass_=True, model="m3", N=640, checks_all_true=True):
    checks = {"valid": True, "target_reached": True, "actual_started_640": True, "invariant_ok": True,
              "color_green": True, "no_stall": True, "postflight_clean": True,
              "connection_limit_evaluable": True, "connection_limit_binding_absent": checks_all_true}
    data = {"pass": pass_, "checks": checks, "result": {"model": model, "concurrency": N}}
    (tmp / "canary-result.json").write_text(json.dumps(data))


def test_canary_only_never_touches_matrix():
    """--canary-only: run_canary() called exactly once; run_initial_matrix/run_extras NEVER called."""
    d = fresh_driver()
    tmp = Path(tempfile.mkdtemp())
    d.FINAL_OUT_ROOT = tmp
    calls = {"canary": 0, "matrix": 0, "extras": 0}
    d.run_canary = lambda: (calls.__setitem__("canary", calls["canary"] + 1), True)[1]
    d.run_initial_matrix = lambda: calls.__setitem__("matrix", calls["matrix"] + 1)
    d.run_extras = lambda: calls.__setitem__("extras", calls["extras"] + 1)
    old_argv = sys.argv
    sys.argv = ["run_phase4_closed_formal.py", "--canary-only"]
    try:
        d.main()
    finally:
        sys.argv = old_argv
    check("--canary-only: run_canary called exactly once", calls["canary"] == 1)
    check("--canary-only: run_initial_matrix never called", calls["matrix"] == 0)
    check("--canary-only: run_extras never called", calls["extras"] == 0)


def test_formal_only_with_valid_existing_canary_skips_canary():
    """--formal-only with a verified PASS canary-result.json: run_canary() NEVER called; matrix runs."""
    d = fresh_driver()
    tmp = Path(tempfile.mkdtemp())
    d.FINAL_OUT_ROOT = tmp
    _canary_artifact(tmp, pass_=True)
    calls = {"canary": 0, "matrix": 0}

    def fail_if_called():
        calls["canary"] += 1
        raise AssertionError("run_canary() must not be called in --formal-only with a valid existing canary")
    d.run_canary = fail_if_called
    d.run_initial_matrix = lambda: calls.__setitem__("matrix", calls["matrix"] + 1)
    d.run_extras = lambda: None
    old_argv = sys.argv
    sys.argv = ["run_phase4_closed_formal.py", "--formal-only"]
    try:
        d.main()
    finally:
        sys.argv = old_argv
    check("--formal-only + valid existing canary: run_canary NOT called", calls["canary"] == 0)
    check("--formal-only + valid existing canary: run_initial_matrix called", calls["matrix"] == 1)
    check("--formal-only + valid existing canary: STOP not triggered", d.STOP["triggered"] is False)


def test_formal_only_missing_canary_artifact_stops():
    d = fresh_driver()
    tmp = Path(tempfile.mkdtemp())
    d.FINAL_OUT_ROOT = tmp  # no canary-result.json written
    calls = {"canary": 0, "matrix": 0}
    d.run_canary = lambda: (calls.__setitem__("canary", calls["canary"] + 1), True)[1]
    d.run_initial_matrix = lambda: calls.__setitem__("matrix", calls["matrix"] + 1)
    old_argv = sys.argv
    sys.argv = ["run_phase4_closed_formal.py", "--formal-only"]
    try:
        d.main()
    finally:
        sys.argv = old_argv
    check("--formal-only + missing canary artifact: run_canary NOT called", calls["canary"] == 0)
    check("--formal-only + missing canary artifact: run_initial_matrix NOT called", calls["matrix"] == 0)
    check("--formal-only + missing canary artifact: STOP triggered", d.STOP["triggered"] is True)


def test_formal_only_failed_canary_stops():
    d = fresh_driver()
    tmp = Path(tempfile.mkdtemp())
    d.FINAL_OUT_ROOT = tmp
    _canary_artifact(tmp, pass_=False)
    calls = {"canary": 0, "matrix": 0}
    d.run_canary = lambda: (calls.__setitem__("canary", calls["canary"] + 1), True)[1]
    d.run_initial_matrix = lambda: calls.__setitem__("matrix", calls["matrix"] + 1)
    old_argv = sys.argv
    sys.argv = ["run_phase4_closed_formal.py", "--formal-only"]
    try:
        d.main()
    finally:
        sys.argv = old_argv
    check("--formal-only + failed canary: run_canary NOT called", calls["canary"] == 0)
    check("--formal-only + failed canary: matrix NOT started", calls["matrix"] == 0)
    check("--formal-only + failed canary: STOP triggered", d.STOP["triggered"] is True)


def test_formal_only_wrong_model_n_stops():
    d = fresh_driver()
    tmp = Path(tempfile.mkdtemp())
    d.FINAL_OUT_ROOT = tmp
    _canary_artifact(tmp, pass_=True, model="m2", N=400)  # wrong cell entirely
    calls = {"canary": 0, "matrix": 0}
    d.run_canary = lambda: (calls.__setitem__("canary", calls["canary"] + 1), True)[1]
    d.run_initial_matrix = lambda: calls.__setitem__("matrix", calls["matrix"] + 1)
    old_argv = sys.argv
    sys.argv = ["run_phase4_closed_formal.py", "--formal-only"]
    try:
        d.main()
    finally:
        sys.argv = old_argv
    check("--formal-only + wrong model/N canary: run_canary NOT called", calls["canary"] == 0)
    check("--formal-only + wrong model/N canary: matrix NOT started", calls["matrix"] == 0)
    check("--formal-only + wrong model/N canary: STOP triggered", d.STOP["triggered"] is True)


def test_default_mode_reuses_existing_canary():
    """Plain invocation (no flag) with an existing valid Canary present must also NOT re-run it --
    this is the exact scenario the orchestration audit was raised for."""
    d = fresh_driver()
    tmp = Path(tempfile.mkdtemp())
    d.FINAL_OUT_ROOT = tmp
    _canary_artifact(tmp, pass_=True)
    calls = {"canary": 0, "matrix": 0}

    def fail_if_called():
        calls["canary"] += 1
        raise AssertionError("run_canary() must not be called by default mode when a valid canary already exists")
    d.run_canary = fail_if_called
    d.run_initial_matrix = lambda: calls.__setitem__("matrix", calls["matrix"] + 1)
    d.run_extras = lambda: None
    old_argv = sys.argv
    sys.argv = ["run_phase4_closed_formal.py"]
    try:
        d.main()
    finally:
        sys.argv = old_argv
    check("default mode + existing valid canary: run_canary NOT called", calls["canary"] == 0)
    check("default mode + existing valid canary: matrix started", calls["matrix"] == 1)


def test_default_mode_runs_fresh_canary_when_none_exists():
    d = fresh_driver()
    tmp = Path(tempfile.mkdtemp())
    d.FINAL_OUT_ROOT = tmp  # nothing written -- genuinely fresh environment
    calls = {"canary": 0, "matrix": 0}
    d.run_canary = lambda: (calls.__setitem__("canary", calls["canary"] + 1), True)[1]
    d.run_initial_matrix = lambda: calls.__setitem__("matrix", calls["matrix"] + 1)
    d.run_extras = lambda: None
    old_argv = sys.argv
    sys.argv = ["run_phase4_closed_formal.py"]
    try:
        d.main()
    finally:
        sys.argv = old_argv
    check("default mode + no existing canary: run_canary called exactly once", calls["canary"] == 1)
    check("default mode + no existing canary: matrix started after canary passes", calls["matrix"] == 1)


def test_initial_order_frozen_shape():
    d = fresh_driver()
    check("INITIAL_ORDER has 18 entries", len(d.INITIAL_ORDER) == 18)
    check("every cell appears exactly 3 times in INITIAL_ORDER", all(d.INITIAL_ORDER.count(c) == 3 for c in d.FORMAL_CELLS))
    check("no cell repeats within a single 6-entry block", all(
        len(set(d.INITIAL_ORDER[i:i + 6])) == 6 for i in (0, 6, 12)))


def main():
    test_confirmed_sustainable_f1()
    test_confirmed_boundary_f2()
    test_confirmed_unstable_f1()
    test_not_confirmed_f2()
    test_ambiguous_then_resolved_confirmed()
    test_ambiguous_then_inconclusive_reachability()
    test_ambiguous_reversal_confirmed_unstable()
    test_n5_three_two_is_inconclusive()
    test_n5_four_one_is_confirmed()
    test_n3_three_zero_confirmed()
    test_n3_one_two_confirmed_unstable()
    test_n3_zero_three_confirmed_unstable()
    test_no_new_labels_used()
    test_invalid_excluded_from_replicate_count()
    test_recoverable_invalid_retry_succeeds()
    test_no_retry_invalid_stops_immediately()
    test_canary_pass()
    test_canary_fail_stops()
    test_boundary_vs_signature_separation_f2()
    test_boundary_vs_signature_separation_f4()
    test_canary_only_never_touches_matrix()
    test_formal_only_with_valid_existing_canary_skips_canary()
    test_formal_only_missing_canary_artifact_stops()
    test_formal_only_failed_canary_stops()
    test_formal_only_wrong_model_n_stops()
    test_default_mode_reuses_existing_canary()
    test_default_mode_runs_fresh_canary_when_none_exists()
    test_initial_order_frozen_shape()

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s): {failures}")
        sys.exit(1)
    print("ALL FORMAL DRIVER SYNTHETIC TESTS PASSED")


if __name__ == "__main__":
    main()
