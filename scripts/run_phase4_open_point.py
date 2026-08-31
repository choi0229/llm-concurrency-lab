#!/usr/bin/env python3
"""Phase 4 Open Unit 8.3 -- run ONE canonical Open Screening point (no geometric progression, no
driver STOP machinery). Mirrors run_phase4_open_screening.py's run_point(): invoke the frozen
canonical harness (scripts/run-phase4-open-benchmark.sh -> scenario 05, final VU formula,
warmup 60 / measure 120, Tomcat/WebClient pools 3200, client.close lifecycle), collect via
scripts/collect_phase4_open_result.py, then move the artifact into the canonical Screening root
docs/test-results/phase4/unit8-open-screening/<model>-r<R>-<label>/ .

This is a canonical MODEL Screening point -- distinct from the Unit 8.2 control-calibration runs at
the same rate (docs/decisions/phase4-open-single-host-ephemeral-headroom.md; Unit 8.3 instruction
sec 19). No socket sampler is attached (Unit 8.2 is the R168 safety authority; sec 4).

Usage: run_phase4_open_point.py <model:m1|m2|m3> <rate> <label>
Prints result.json to stdout; exit 0 if VALID, 3 if INVALID, 1 on harness/collector failure.
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
HARNESS_OUT_ROOT = ROOT / "docs" / "test-results" / "phase4" / "unit8-open-harness"
FINAL_OUT_ROOT = ROOT / "docs" / "test-results" / "phase4" / "unit8-open-screening"
WARMUP_SEC = 60
MEASUREMENT_SEC = 120


def main():
    model, rate, label = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    assert model in ("m1", "m2", "m3")
    name = f"{model}-r{rate}-{label}"
    if (HARNESS_OUT_ROOT / name).exists() or (FINAL_OUT_ROOT / name).exists():
        print(f"REFUSING: {name} already exists (no overwrite of a canonical point)", file=sys.stderr)
        sys.exit(1)
    print(f"[{time.strftime('%H:%M:%S')}] RUN {model} R={rate} label={label}", flush=True)
    proc = subprocess.run([str(HARNESS), model, str(rate), label, str(WARMUP_SEC), str(MEASUREMENT_SEC)],
                          cwd=str(ROOT), capture_output=True, text=True,
                          timeout=WARMUP_SEC + MEASUREMENT_SEC + 400)
    hdir = HARNESS_OUT_ROOT / name
    if not hdir.exists():
        print(f"HARNESS FATAL rc={proc.returncode}\n{proc.stdout[-3000:]}\n---STDERR---\n{proc.stderr[-1500:]}")
        sys.exit(1)
    (hdir / "driver-harness-stdout.log").write_text(proc.stdout + "\n---STDERR---\n" + proc.stderr)
    cproc = subprocess.run([sys.executable, str(COLLECTOR), str(hdir)], capture_output=True, text=True)
    rp = hdir / "result.json"
    if not rp.exists():
        print(f"COLLECTOR FATAL stdout={cproc.stdout[-1500:]} stderr={cproc.stderr[-1500:]}")
        sys.exit(1)
    result = json.loads(rp.read_text())
    fdir = FINAL_OUT_ROOT / name
    FINAL_OUT_ROOT.mkdir(parents=True, exist_ok=True)
    shutil.move(str(hdir), str(fdir))
    result["_final_dir"] = str(fdir.relative_to(ROOT))
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("valid") else 3)


if __name__ == "__main__":
    main()
