#!/usr/bin/env python3
"""Synthetic (no-load) regression for the Open Formal Canary gate correction.

Verifies, without running any benchmark:
  1. the TIME_WAIT gate is judged on `time_wait_75s_post_drain` ONLY (not `at_drain_end`)
  2. the retained threshold is <= 3000 (unchanged from the original freeze)
  3. fail-closed: a missing (None) or malformed post-decay sample FAILS the check
  4. `at_drain_end` ~= 10k does NOT by itself fail the gate
  5. a fully-clean synthetic result passes every canary check
  6. `matrix` refuses to run with no PASS canary-result*.json
  7. importing the module does not execute a run
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import run_phase4_open_formal as F  # noqa: E402

FAILS = []


def check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILS.append(name)


def base_result(post_decay, at_drain_end=9781):
    return {
        "valid": True,
        "client_cohort": {"actual_started": 168 * F.MEASUREMENT_SEC, "dropped_iterations": 0,
                          "invariant_ok": True},
        "model_outcome": {"color": "GREEN",
                          "server_outcomes_whole_process_estimate": {"upstream_error": 0}},
        "client_latency": {"clock_integrity_ok": True},
        "implementation_specific": {"tomcat_connections_current_peak": {"peak": 1364},
                                    "tomcat_connections_configured_max": 3200},
        "stall_check": {"stall_detected": False},
        "postflight_clean": True,
        "_time_wait_snapshot": {"before": 0, "at_drain_end": at_drain_end, "post_decay_75s": post_decay},
    }


print("== TIME_WAIT gate ==")
check("post_decay 2500 -> pass",  F.canary_checks(base_result(2500))["time_wait_returns_toward_baseline"] is True)
check("post_decay 3000 (boundary) -> pass", F.canary_checks(base_result(3000))["time_wait_returns_toward_baseline"] is True)
check("post_decay 3001 -> fail",  F.canary_checks(base_result(3001))["time_wait_returns_toward_baseline"] is False)
check("post_decay None (missing) -> fail-closed", F.canary_checks(base_result(None))["time_wait_returns_toward_baseline"] is False)
check("post_decay 'garbage' -> fail-closed", F.canary_checks(base_result("garbage"))["time_wait_returns_toward_baseline"] is False)
check("at_drain_end 9781 alone does NOT fail (post_decay 200)",
      F.canary_checks(base_result(200, at_drain_end=9781))["time_wait_returns_toward_baseline"] is True)

print("== threshold retained at 3000 (not 1500) ==")
check("post_decay 2000 -> pass (would fail a 1500 threshold)",
      F.canary_checks(base_result(2000))["time_wait_returns_toward_baseline"] is True)

print("== fully-clean synthetic result passes every canary check ==")
clean = F.canary_checks(base_result(150))
# add the two gateway.log-derived checks the way run_canary would for a clean run
clean["no_bindexception"] = True
clean["no_eaddrnotavail"] = True
for k, v in clean.items():
    check(f"clean.{k}", v is True)

print("== matrix refuses without a PASS canary-result ==")
with tempfile.TemporaryDirectory() as td:
    orig = F.FINAL_OUT_ROOT
    F.FINAL_OUT_ROOT = Path(td)
    try:
        (Path(td) / "canary-result.json").write_text(json.dumps({"pass": False}))
        rc = subprocess.run([sys.executable, "-c",
                             "import sys; sys.path.insert(0,'scripts'); import run_phase4_open_formal as F;"
                             f"F.FINAL_OUT_ROOT=__import__('pathlib').Path({td!r});"
                             "F.run_matrix()"], capture_output=True, text=True)
        check("run_matrix exits nonzero with only pass:false canary", rc.returncode != 0)
        check("run_matrix logs REFUSING", "REFUSING matrix" in (rc.stdout + rc.stderr))
    finally:
        F.FINAL_OUT_ROOT = orig

print("== import side-effect free ==")
rc = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0,'scripts'); import run_phase4_open_formal"],
                    capture_output=True, text=True)
check("bare import runs nothing / exits 0", rc.returncode == 0 and "RUN m2" not in rc.stdout)

print()
if FAILS:
    print(f"SYNTHETIC REGRESSION: {len(FAILS)} FAILURE(S): {FAILS}")
    sys.exit(1)
print("SYNTHETIC REGRESSION: ALL PASS")
