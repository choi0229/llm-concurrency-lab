#!/usr/bin/env python3
"""Synthetic (no-load) regression for the Open Formal matrix driver logic."""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import run_phase4_open_formal as F  # noqa: E402

FAILS = []


def ck(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILS.append(name)


print("== frozen constants ==")
ck("12-run order literal", F.INITIAL_ORDER ==
   ["F1", "F3", "F2", "F4", "F4", "F2", "F3", "F1", "F2", "F1", "F4", "F3"])
ck("each cell x3", all(F.INITIAL_ORDER.count(c) == 3 for c in F.FORMAL_CELLS))
ck("TIME_WAIT threshold retained 3000", F.TIME_WAIT_POST_DECAY_MAX == 3000)
ck("F1 predicate GREEN->pass", F.FORMAL_CELLS["F1"]["predicate"]({"color": "GREEN"}) is True)
ck("F1 predicate AMBER->fail", F.FORMAL_CELLS["F1"]["predicate"]({"color": "AMBER"}) is False)
ck("F2 predicate AMBER->pass (non-green)", F.FORMAL_CELLS["F2"]["predicate"]({"color": "AMBER"}) is True)
ck("F2 predicate RED->pass (non-green, boundary not colour)", F.FORMAL_CELLS["F2"]["predicate"]({"color": "RED"}) is True)
ck("F2 predicate GREEN->fail", F.FORMAL_CELLS["F2"]["predicate"]({"color": "GREEN"}) is False)
ck("F3/F4 predicate GREEN->pass", F.FORMAL_CELLS["F3"]["predicate"]({"color": "GREEN"}) and F.FORMAL_CELLS["F4"]["predicate"]({"color": "GREEN"}))

print("== confirm_cell (n=3) ==")


def set_reps(cid, passes, fails, extra=0):
    F.STATE[cid]["reps"] = ([{"valid": True, "boundary_pass": True, "signature_match": True}] * passes
                            + [{"valid": True, "boundary_pass": False, "signature_match": False}] * fails
                            + [{"valid": False, "boundary_pass": False}] * extra)


set_reps("F3", 3, 0)
ck("F3 3/0 -> CONFIRMED SUSTAINABLE", F.confirm_cell("F3") == "CONFIRMED SUSTAINABLE")
set_reps("F2", 3, 0)
ck("F2 3/0 -> CONFIRMED (boundary, not 'sustainable')", F.confirm_cell("F2") == "CONFIRMED")
set_reps("F3", 2, 1)
ck("F3 2/1 -> AMBIGUOUS", F.confirm_cell("F3") == "AMBIGUOUS")
set_reps("F3", 1, 2)
ck("F3 1/2 -> CONFIRMED UNSTABLE", F.confirm_cell("F3") == "CONFIRMED UNSTABLE")
set_reps("F2", 0, 3)
ck("F2 0/3 -> NOT CONFIRMED", F.confirm_cell("F2") == "NOT CONFIRMED")
set_reps("F3", 2, 0)
ck("F3 n=2 -> None (not enough)", F.confirm_cell("F3") is None)

print("== confirm_cell (n=5 from AMBIGUOUS seed) ==")
set_reps("F3", 4, 1)
ck("F3 4/1 -> CONFIRMED SUSTAINABLE", F.confirm_cell("F3") == "CONFIRMED SUSTAINABLE")
set_reps("F3", 3, 2)
ck("F3 3/2 -> INCONCLUSIVE RANGE", F.confirm_cell("F3") == "INCONCLUSIVE RANGE")
set_reps("F3", 2, 3)
ck("F3 2/3 -> CONFIRMED UNSTABLE", F.confirm_cell("F3") == "CONFIRMED UNSTABLE")
set_reps("F2", 4, 1)
ck("F2 4/1 -> CONFIRMED", F.confirm_cell("F2") == "CONFIRMED")
set_reps("F2", 3, 2)
ck("F2 3/2 -> INCONCLUSIVE RANGE", F.confirm_cell("F2") == "INCONCLUSIVE RANGE")
for c in F.FORMAL_CELLS:
    F.STATE[c]["reps"] = []

print("== driver-side validity gates ==")


def base(model="m2", tw=0, valid=True, invalid=None, impl=None, dur=8.0):
    return {"valid": valid, "invalid_reasons": invalid or [],
            "model_outcome": {"color": "GREEN"},
            "client_latency": {"completed_duration_p95_s": dur},
            "implementation_specific": impl or {},
            "_time_wait_snapshot": {"post_decay_75s": tw}}


ok, rs = F._driver_side_valid("F3", base(tw=0))
ck("clean m2 rep -> valid", ok and rs == [])
ok, rs = F._driver_side_valid("F3", base(tw=3001))
ck("tw post-decay 3001 -> time_wait_gate invalid", (not ok) and "time_wait_gate" in rs)
ok, rs = F._driver_side_valid("F3", base(tw=None))
ck("tw post-decay None -> fail-closed time_wait_gate", (not ok) and "time_wait_gate" in rs)
ok, rs = F._driver_side_valid("F3", base(tw=3000))
ck("tw post-decay 3000 boundary -> valid", ok)
ok, rs = F._driver_side_valid("F3", base(dur=6.0))
ck("dur p95 6.0s (< 6.5 floor) -> clock xcheck invalid", (not ok) and "clock_integrity_anomaly_xcheck" in rs)
ok, rs = F._driver_side_valid("F1", base(model="m1", invalid=["tomcat_connections_non_binding"], valid=False))
ck("m1 tomcat binding -> invalid & NO_RETRY", (not ok) and ("tomcat_connections_non_binding" in (set(rs) & F.NO_RETRY_KEYS)))
impl_bind = {"reactor_connection_active_peak": {"peak": 3200}, "max_connections_configured": 3200,
             "reactor_connection_pending_peak": {"peak": 0}}
ok, rs = F._driver_side_valid("F4", base(model="m3", impl=impl_bind))
ck("m3 active peak == pool max -> m3_pool_binding invalid (NO_RETRY)",
   (not ok) and "m3_pool_binding" in rs and "m3_pool_binding" in F.NO_RETRY_KEYS)
impl_pend = {"reactor_connection_active_peak": {"peak": 1400}, "max_connections_configured": 3200,
             "reactor_connection_pending_peak": {"peak": 5}}
ok, rs = F._driver_side_valid("F4", base(model="m3", impl=impl_pend))
ck("m3 transient pending>0 but active < max -> NOT binding (valid)", ok)

print("== NO_RETRY set membership ==")
for k in ["environment_stall_false", "clock_integrity_ok", "pid_match", "internal_error_eq_0",
          "postflight_clean", "tomcat_connections_non_binding", "cohort_invariant_ok",
          "time_wait_gate", "provenance_drift", "m3_pool_binding"]:
    ck(f"NO_RETRY contains {k}", k in F.NO_RETRY_KEYS)
for k in ["k6_exit_zero", "rss_samples_valid", "prometheus_sampling_valid", "dropped_eq_0"]:
    ck(f"NO_RETRY does NOT contain recoverable {k}", k not in F.NO_RETRY_KEYS)

print("== matrix refuses with no PASS canary ==")
rc = subprocess.run([sys.executable, "-c",
                     "import sys,pathlib,tempfile; sys.path.insert(0,'scripts'); import run_phase4_open_formal as F;"
                     "d=tempfile.mkdtemp(); F.FINAL_OUT_ROOT=pathlib.Path(d);"
                     "open(d+'/canary-result.json','w').write('{\"pass\": false}'); F.run_matrix()"],
                    capture_output=True, text=True)
ck("run_matrix exits nonzero without PASS canary", rc.returncode != 0)
ck("run_matrix says REFUSING / no Canary load", "REFUSING matrix" in (rc.stdout + rc.stderr))

print()
if FAILS:
    print(f"MATRIX SYNTHETIC: {len(FAILS)} FAILURE(S): {FAILS}")
    sys.exit(1)
print("MATRIX SYNTHETIC: ALL PASS")
