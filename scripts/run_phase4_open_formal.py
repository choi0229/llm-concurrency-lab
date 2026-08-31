#!/usr/bin/env python3
"""Phase 4 Open Formal -- thin orchestration over the frozen canonical Open harness.

Reuses scripts/run-phase4-open-benchmark.sh (scenario 05, final VU formula, Tomcat/WebClient pools
3200, client.close lifecycle) with the FORMAL window (warmup 120 / measurement 300) and
scripts/collect_phase4_open_result.py (window-agnostic). No production source change, no new
measurement code -- only cell/order/confirmation bookkeeping and the Stability Canary gate.

Protocol freeze: docs/test-plan/phase4-open-formal-protocol.md

Subcommands:
  canary   -- run the M2 R168 Stability Canary exactly once, gate it, write canary-result.json.
              Does NOT start the matrix on PASS.
  matrix   -- run the 12-run initial Formal matrix in the frozen order (+ AMBIGUOUS extras).
              REQUIRES a PASS canary-result.json to already exist. Not invoked automatically.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
HARNESS = ROOT / "scripts" / "run-phase4-open-benchmark.sh"
COLLECTOR = ROOT / "scripts" / "collect_phase4_open_result.py"
HARNESS_OUT_ROOT = ROOT / "docs" / "test-results" / "phase4" / "unit8-open-harness"
FINAL_OUT_ROOT = ROOT / "docs" / "test-results" / "phase4" / "unit9-open-formal"
WARMUP_SEC = 120
MEASUREMENT_SEC = 300

# ---- Frozen Formal cells (protocol section 3) ----
FORMAL_CELLS = {
    "F1": {"model": "m1", "rate": 6, "role": "MSAR lower endpoint", "expected": "SUSTAINABLE",
           "predicate": lambda mo: mo.get("color") == "GREEN", "signature": "GREEN"},
    "F2": {"model": "m1", "rate": 7, "role": "MSAR upper endpoint", "expected": "BOUNDARY",
           "predicate": lambda mo: mo.get("color") != "GREEN", "signature": "AMBER"},
    "F3": {"model": "m2", "rate": 168, "role": "control-censored lower-bound", "expected": "SUSTAINABLE",
           "predicate": lambda mo: mo.get("color") == "GREEN", "signature": "GREEN"},
    "F4": {"model": "m3", "rate": 168, "role": "control-censored lower-bound", "expected": "SUSTAINABLE",
           "predicate": lambda mo: mo.get("color") == "GREEN", "signature": "GREEN"},
}
INITIAL_ORDER = (["F1", "F3", "F2", "F4"] + ["F4", "F2", "F3", "F1"] + ["F2", "F1", "F4", "F3"])
assert len(INITIAL_ORDER) == 12 and all(INITIAL_ORDER.count(c) == 3 for c in FORMAL_CELLS)
CANARY_CELL = {"model": "m2", "rate": 168}
MAX_EXTRA_REPS = 2
ARRIVAL_TOL = 0.05
TIME_WAIT_POST_DECAY_MAX = 3000  # frozen threshold (unchanged from the original Canary freeze)

# Frozen files whose hash must not change once the first F1 rep1 load starts (protocol section 9/25).
FROZEN_FILES = [
    "gateway-phase4-platform-queue/build/libs/gateway-phase4-platform-queue-0.1.0.jar",
    "gateway-phase4-virtual-thread/build/libs/gateway-phase4-virtual-thread-0.1.0.jar",
    "gateway-phase4-webflux/build/libs/gateway-phase4-webflux-0.1.0.jar",
    "gateway-phase4-platform-queue/src/main/java/com/llmconcurrencylab/phase4/common/BlockingMockLlmRelay.java",
    "gateway-phase4-platform-queue/src/main/java/com/llmconcurrencylab/phase4/common/MockLlmClient.java",
    "mock-llm-fastapi/app/main.py",
    "load-test-k6/scenarios/05-phase4-open-arrival-rate.js",
    "scripts/run-phase4-open-benchmark.sh",
    "scripts/collect_phase4_open_result.py",
    "scripts/check_phase4_stall.py",
    "monitoring/prometheus/prometheus-phase4.yml",
    "scripts/run_phase4_open_formal.py",
]

# INVALID reasons that trigger an immediate Formal STOP (no retry) -- protocol section 24.
NO_RETRY_KEYS = {
    "environment_stall_false", "clock_integrity_ok", "pid_match", "internal_error_eq_0",
    "postflight_clean", "tomcat_connections_non_binding", "control_range_ok",
    "cohort_invariant_ok",              # population divergence
    "time_wait_gate", "provenance_drift", "m3_pool_binding", "clock_integrity_anomaly_xcheck",
}

STATE = {cid: {"reps": [], "confirmation": None, "extras_used": 0} for cid in FORMAL_CELLS}
GLOBAL_LOG = []
STOP = {"triggered": False, "reason": None}
_PROV_BASELINE = {}


def log(m):
    line = f"[{time.strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)
    GLOBAL_LOG.append(line)


def sha256_of(rel):
    p = ROOT / rel
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def record_provenance(fname):
    prov = {rel: sha256_of(rel) for rel in FROZEN_FILES}
    FINAL_OUT_ROOT.mkdir(parents=True, exist_ok=True)
    (FINAL_OUT_ROOT / fname).write_text(json.dumps(prov, indent=2))
    log(f"Provenance recorded ({fname})")
    return prov


def provenance_drift():
    """True if any frozen file's hash changed vs the formal-start baseline."""
    if not _PROV_BASELINE:
        return False
    now = {rel: sha256_of(rel) for rel in FROZEN_FILES}
    return [rel for rel in FROZEN_FILES if now.get(rel) != _PROV_BASELINE.get(rel)]


def trigger_stop(reason):
    STOP["triggered"] = True
    STOP["reason"] = reason
    log(f"*** OPEN FORMAL STOP: {reason} ***")


def m3_pool_check(result):
    """(evaluable, binding) for M3 reactor connection pool. Transient pending is diagnostic, not
    binding (protocol section 14); binding == active peak reached the configured pool ceiling."""
    impl = result.get("implementation_specific", {}) or {}
    active = (impl.get("reactor_connection_active_peak") or {})
    active_peak = active.get("peak") if isinstance(active, dict) else active
    max_conf = impl.get("max_connections_configured")
    evaluable = active_peak is not None and max_conf is not None
    binding = evaluable and active_peak >= max_conf
    return evaluable, binding


def clock_xcheck(result):
    """Closed F6-style extra guard: a completed-stream duration far SHORTER than theoretical pacing
    (~1000 + 34*200 = 7800ms) points at a wall-clock discontinuity. Returns True if anomalous."""
    dur_p95 = ((result.get("client_latency") or {}).get("completed_duration_p95_s"))
    if not isinstance(dur_p95, (int, float)):
        return False
    return dur_p95 < 6.5  # >15% under the ~7.8s theoretical floor -> suspect


def start_light_transport_sampler(out_csv, iters):
    """5 s cadence, self-terminating after `iters` samples: total tcp / TIME_WAIT /
    ESTABLISHED->:18102 / ESTABLISHED->:8000. One netstat per 5 s -- negligible vs the Unit 8.1/8.2
    per-socket raw sampler (protocol section 7). Fixed iteration count so it always stops."""
    script = (
        'echo "epoch_s,tcp_total,time_wait,est_to_18102,est_to_8000" > "%s"; '
        'for _ in $(seq 1 %d); do '
        'n=$(netstat -n -p tcp 2>/dev/null); '
        'echo "$(date +%%s),$(printf \'%%s\' "$n"|grep -c .),$(printf \'%%s\' "$n"|grep -c TIME_WAIT),'
        '$(printf \'%%s\' "$n"|grep -c "\\.18102 .*ESTABLISHED"),$(printf \'%%s\' "$n"|grep -c "\\.8000 .*ESTABLISHED")" >> "%s"; '
        'sleep 5; done' % (out_csv, iters, out_csv)
    )
    return subprocess.Popen(["bash", "-c", script])


def _tw_count():
    """Current host TIME_WAIT count as int, or None if netstat failed / output malformed
    (fail-closed: a missing sample must not be coerced to 0)."""
    p = subprocess.run(["bash", "-c", "netstat -n -p tcp 2>/dev/null | grep -c TIME_WAIT"],
                       capture_output=True, text=True)
    s = (p.stdout or "").strip()
    return int(s) if s.isdigit() else None


def run_point(model, rate, label):
    name = f"{model}-r{rate}-{label}"
    if (HARNESS_OUT_ROOT / name).exists() or (FINAL_OUT_ROOT / name).exists():
        return {"valid": False, "invalid_reasons": ["output_dir_exists"], "model": model, "target_rate": rate}
    log(f"RUN {model} R={rate} label={label} (warmup {WARMUP_SEC}s / measure {MEASUREMENT_SEC}s)")
    tw_before = _tw_count()
    sampler_csv = f"/tmp/phase4-open-formal-{name}-transport.csv"
    n_iters = (WARMUP_SEC + MEASUREMENT_SEC) // 5 + 24  # covers startup + drain
    sampler = start_light_transport_sampler(sampler_csv, n_iters)
    proc = subprocess.run([str(HARNESS), model, str(rate), label, str(WARMUP_SEC), str(MEASUREMENT_SEC)],
                          cwd=str(ROOT), capture_output=True, text=True,
                          timeout=WARMUP_SEC + MEASUREMENT_SEC + 600)
    sampler.terminate()
    hdir = HARNESS_OUT_ROOT / name
    if not hdir.exists():
        log(f"HARNESS FATAL rc={proc.returncode}\n{proc.stdout[-3000:]}\n{proc.stderr[-1500:]}")
        return {"valid": False, "invalid_reasons": ["harness_no_output_dir"], "model": model, "target_rate": rate}
    (hdir / "driver-harness-stdout.log").write_text(proc.stdout + "\n---STDERR---\n" + proc.stderr)
    if Path(sampler_csv).exists():
        shutil.move(sampler_csv, str(hdir / "light-transport-telemetry.csv"))
    tw_at_drain = _tw_count()
    # "returns toward baseline" must be checked AFTER the ~2*MSL (30s on this host) TIME_WAIT decay,
    # not at drain-end: at R=168 the end-of-run TIME_WAIT residue is ~10k (Unit 8.2 TWa+TWb), which
    # is the expected CONTROL_SAFE steady state, not a leak. 75s is a conservative post-decay
    # sampling point for THIS host (>> 2*MSL); it is not claimed to be a universal constant.
    time.sleep(75)
    tw_post_decay = _tw_count()
    (hdir / "time-wait-snapshot.json").write_text(json.dumps(
        {"time_wait_before_run": tw_before, "time_wait_at_drain_end": tw_at_drain,
         "time_wait_75s_post_drain": tw_post_decay,
         "note": "verdict is on time_wait_75s_post_drain only; at_drain_end is diagnostic. "
                 "null == sample failed -> fail-closed."}, indent=2))
    cproc = subprocess.run([sys.executable, str(COLLECTOR), str(hdir)], capture_output=True, text=True)
    rp = hdir / "result.json"
    if not rp.exists():
        log(f"COLLECTOR FATAL stdout={cproc.stdout[-1500:]} stderr={cproc.stderr[-1500:]}")
        return {"valid": False, "invalid_reasons": ["collector_failed"], "model": model, "target_rate": rate}
    result = json.loads(rp.read_text())
    fdir = FINAL_OUT_ROOT / name
    FINAL_OUT_ROOT.mkdir(parents=True, exist_ok=True)
    shutil.move(str(hdir), str(fdir))
    result["_final_dir"] = str(fdir.relative_to(ROOT))
    result["_time_wait_snapshot"] = {"before": tw_before, "at_drain_end": tw_at_drain,
                                     "post_decay_75s": tw_post_decay}
    return result


def canary_checks(r):
    cc = r.get("client_cohort", {}) or {}
    mo = r.get("model_outcome", {}) or {}
    lat = r.get("client_latency", {}) or {}
    impl = r.get("implementation_specific", {}) or {}
    val = r.get("validity", {}) if isinstance(r.get("validity"), dict) else {}
    tw = r.get("_time_wait_snapshot", {})
    started = cc.get("actual_started") or 0
    exp = CANARY_CELL["rate"] * MEASUREMENT_SEC
    tomcat_peak = (impl.get("tomcat_connections_current_peak", {}) or {}).get("peak", 0)
    tomcat_max = impl.get("tomcat_connections_configured_max", 3200)
    return {
        "valid": r.get("valid") is True,
        "dropped_eq_0": cc.get("dropped_iterations") == 0,
        "arrival_within_5pct": exp > 0 and abs(started - exp) <= ARRIVAL_TOL * exp,
        "cohort_invariant_ok": cc.get("invariant_ok") is True,
        "color_green": mo.get("color") == "GREEN",
        "no_stall": (r.get("stall_check", {}) or {}).get("stall_detected") is False,
        "clock_integrity_ok": lat.get("clock_integrity_ok") is True,
        "postflight_clean": r.get("postflight_clean") is True,
        "tomcat_non_binding": 0 < tomcat_peak < tomcat_max,
        "no_upstream_error": (mo.get("server_outcomes_whole_process_estimate", {}) or {}).get("upstream_error", 0) == 0,
        # Checked AFTER the ~2*MSL TIME_WAIT decay (75s post-drain). Threshold UNCHANGED from the
        # original freeze (<= 3000) -- only the SAMPLE TIME was corrected. Fail-closed: a missing /
        # malformed post-decay sample (None) fails the check.
        "time_wait_returns_toward_baseline": isinstance(tw.get("post_decay_75s"), int)
                                             and tw.get("post_decay_75s") <= 3000,
    }


def run_canary(label="canary"):
    FINAL_OUT_ROOT.mkdir(parents=True, exist_ok=True)
    log(f"=== OPEN FORMAL STABILITY CANARY: M2 R168 label={label} (excluded from the 12-run matrix) ===")
    r = run_point(CANARY_CELL["model"], CANARY_CELL["rate"], label)
    checks = canary_checks(r)
    # bindexception / eaddr scan of THIS run's gateway.log (via the actual final dir)
    be = eaddr = -1
    fd = r.get("_final_dir")
    gpath = (ROOT / fd / "gateway.log") if fd else None
    if gpath and gpath.exists():
        g = gpath.read_text()
        be = g.count("BindException")
        eaddr = g.count("assign requested address")
    checks["no_bindexception"] = be == 0
    checks["no_eaddrnotavail"] = eaddr == 0
    passed = all(checks.values())
    out = {"pass": passed, "label": label, "checks": checks,
           "bindexception_count": be, "eaddrnotavail_count": eaddr,
           "time_wait_snapshot": r.get("_time_wait_snapshot"),
           "cell": CANARY_CELL, "window": {"warmup_sec": WARMUP_SEC, "measurement_sec": MEASUREMENT_SEC},
           "result": r}
    (FINAL_OUT_ROOT / f"canary-result-{label}.json").write_text(json.dumps(out, indent=2))
    log(f"CANARY -> {'PASS' if passed else 'FAIL'}  checks={json.dumps(checks)}")
    if not passed:
        log("CANARY FAIL -> STOP. No matrix. Artifacts preserved.")
    else:
        log("CANARY PASS. Matrix NOT auto-started -- await explicit approval (protocol section 7).")
    print(json.dumps(out, indent=2))
    return passed


def _driver_side_valid(cid, r):
    """Layer the frozen driver-side gates on top of the collector's own validity. Returns
    (is_valid, reasons_list)."""
    reasons = list(r.get("invalid_reasons", []))
    if not r.get("valid"):
        pass  # collector reasons already in `reasons`
    tw = r.get("_time_wait_snapshot", {}) or {}
    pd = tw.get("post_decay_75s")
    if not (isinstance(pd, int) and pd <= TIME_WAIT_POST_DECAY_MAX):
        reasons.append("time_wait_gate")
    if FORMAL_CELLS[cid]["model"] == "m3":
        evaluable, binding = m3_pool_check(r)
        if not evaluable:
            reasons.append("m3_connection_metrics_not_evaluable")
        if binding:
            reasons.append("m3_pool_binding")
    if clock_xcheck(r):
        reasons.append("clock_integrity_anomaly_xcheck")
    drift = provenance_drift()
    if drift:
        reasons.append("provenance_drift")
        log(f"PROVENANCE DRIFT: {drift}")
    reasons = sorted(set(reasons))
    return (len(reasons) == 0), reasons


def execute_rep(cid, label):
    """One rep with the frozen invalid/retry policy (protocol section 24). Returns
    (rep_dict_or_None, stopped_bool)."""
    cell = FORMAL_CELLS[cid]
    r = run_point(cell["model"], cell["rate"], label)
    ok, reasons = _driver_side_valid(cid, r)

    def _mk(rr, valid_flag):
        mo = rr.get("model_outcome", {}) or {}
        return {"valid": valid_flag, "label": rr.get("_run_label", label),
                "dir": rr.get("_final_dir"),
                "color": mo.get("color"), "classification": mo.get("classification"),
                "boundary_pass": bool(valid_flag and cell["predicate"](mo)),
                "signature_match": bool(valid_flag and mo.get("color") == cell["signature"]),
                "reliability_pass": mo.get("reliability_pass"),
                "invalid_reasons": rr.get("invalid_reasons", []) if valid_flag else reasons,
                "time_wait_snapshot": rr.get("_time_wait_snapshot")}

    if ok:
        return _mk(r, True), False
    hard = set(reasons) & NO_RETRY_KEYS
    if hard:
        log(f"NO-RETRY invalid {hard} for {cid} {label} -- Formal STOP")
        trigger_stop(f"{cid} {label}: no-retry INVALID ({sorted(hard)})")
        return _mk(r, False), True
    log(f"INVALID (recoverable) for {cid} {label}: {reasons} -- one fresh retry")
    r2 = run_point(cell["model"], cell["rate"], f"{label}-retry1")
    ok2, reasons2 = _driver_side_valid(cid, r2)
    if ok2:
        log(f"Retry succeeded for {cid} {label}")
        return _mk(r2, True), False
    log(f"Retry ALSO invalid for {cid} {label}: {reasons2} -- Formal STOP")
    trigger_stop(f"{cid} {label}: original AND retry both INVALID")
    return _mk(r2, False), True


def confirm_cell(cid):
    cell = FORMAL_CELLS[cid]
    valid = [x for x in STATE[cid]["reps"] if x["valid"]]
    n = len(valid)
    passes = sum(1 for x in valid if x["boundary_pass"])
    fails = n - passes
    sustainable = cell["expected"] == "SUSTAINABLE"

    def lab(is_pass):
        if is_pass:
            return "CONFIRMED SUSTAINABLE" if sustainable else "CONFIRMED"
        return "CONFIRMED UNSTABLE" if sustainable else "NOT CONFIRMED"

    if n < 3:
        return None
    if n == 3:
        if passes == 3:
            return lab(True)
        if fails >= 2:
            return lab(False)
        return "AMBIGUOUS"
    if n >= 5:
        if fails <= 1:
            return lab(True)
        if fails >= 3:
            return lab(False)
        return "INCONCLUSIVE RANGE"
    return None


def _write_summary(prov_start):
    summary = {
        "unit": "9-open-formal", "protocol": "docs/test-plan/phase4-open-formal-protocol.md",
        "window": {"warmup_sec": WARMUP_SEC, "measurement_sec": MEASUREMENT_SEC},
        "frozen_order": INITIAL_ORDER,
        "stop_triggered": STOP["triggered"], "stop_reason": STOP["reason"],
        "provenance_formal_start": prov_start,
        "provenance_drift_at_end": provenance_drift() or False,
        "canary": {"pass": True, "attempts": 2,
                   "note": "attempt 1 gate-instrumentation-invalid; attempt 2 PASS. Excluded from Formal."},
        "cells": {}}
    for cid, cell in FORMAL_CELLS.items():
        st = STATE[cid]
        summary["cells"][cid] = {
            "model": cell["model"], "rate": cell["rate"], "role": cell["role"],
            "expected": cell["expected"], "expected_signature": cell["signature"],
            "confirmation": st["confirmation"], "extras_used": st["extras_used"],
            "reps": st["reps"],
        }
    (FINAL_OUT_ROOT / "formal-summary.json").write_text(json.dumps(summary, indent=2))
    (FINAL_OUT_ROOT / "formal-driver-log.txt").write_text("\n".join(GLOBAL_LOG))
    return summary


def run_matrix():
    global _PROV_BASELINE
    passes = [p for p in FINAL_OUT_ROOT.glob("canary-result*.json")
              if json.loads(p.read_text()).get("pass") is True]
    if not passes:
        log("REFUSING matrix: no PASS canary-result*.json present (fail-closed). No Canary load will be run.")
        sys.exit(1)
    log(f"Canary gate: {len(passes)} PASS canary-result file(s) present -> proceeding. NO new Canary load.")

    # sleep prevention for the driver's whole lifetime (protocol section 10)
    caff = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
    log(f"caffeinate -i -w {os.getpid()} started (pid {caff.pid})")

    _PROV_BASELINE = record_provenance("provenance-formal-start.json")
    log("=== OPEN FORMAL 12-RUN MATRIX (frozen order) ===")
    idx = {c: 0 for c in FORMAL_CELLS}
    for cid in INITIAL_ORDER:
        if STOP["triggered"]:
            break
        idx[cid] += 1
        rep, stopped = execute_rep(cid, f"{cid.lower()}-rep{idx[cid]}")
        STATE[cid]["reps"].append(rep)
        STATE[cid]["confirmation"] = confirm_cell(cid)
        _write_summary(_PROV_BASELINE)
        log(f"{cid} rep{idx[cid]}: valid={rep['valid']} color={rep['color']} "
            f"boundary_pass={rep['boundary_pass']} signature_match={rep['signature_match']} "
            f"-> confirmation={STATE[cid]['confirmation']}")
        if stopped:
            break

    # AMBIGUOUS extras -- only after all 12 initial reps, cell-ID order, max 2 per cell
    if not STOP["triggered"]:
        for cid in sorted(FORMAL_CELLS):
            if STATE[cid]["confirmation"] != "AMBIGUOUS":
                continue
            log(f"{cid}: AMBIGUOUS after initial 3 -- up to {MAX_EXTRA_REPS} extra reps")
            base = 3
            for _ in range(MAX_EXTRA_REPS):
                if STOP["triggered"]:
                    break
                base += 1
                rep, stopped = execute_rep(cid, f"{cid.lower()}-rep{base}")
                STATE[cid]["reps"].append(rep)
                STATE[cid]["extras_used"] += 1
                STATE[cid]["confirmation"] = confirm_cell(cid)
                _write_summary(_PROV_BASELINE)
                log(f"{cid} rep{base} (extra): valid={rep['valid']} color={rep['color']} "
                    f"boundary_pass={rep['boundary_pass']} -> confirmation={STATE[cid]['confirmation']}")
                if stopped or STATE[cid]["confirmation"] != "AMBIGUOUS":
                    break

    summary = _write_summary(_PROV_BASELINE)
    print(json.dumps({"stop_triggered": STOP["triggered"], "stop_reason": STOP["reason"],
                      "cells": {c: {"confirmation": STATE[c]["confirmation"],
                                    "valid_reps": sum(1 for x in STATE[c]["reps"] if x["valid"]),
                                    "boundary_pass": sum(1 for x in STATE[c]["reps"] if x["valid"] and x["boundary_pass"])}
                                for c in FORMAL_CELLS}}, indent=2))
    return summary


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "canary"
    if cmd == "canary":
        label = sys.argv[2] if len(sys.argv) > 2 else "canary"
        ok = run_canary(label)
        sys.exit(0 if ok else 3)
    elif cmd == "matrix":
        run_matrix()
    else:
        print("usage: run_phase4_open_formal.py [canary [label] | matrix]", file=sys.stderr)
        sys.exit(2)
