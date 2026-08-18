#!/usr/bin/env bash
# Unit 6 formal Benchmark matrix: 3 configs x (LOW x1, STRESS x3, OVERLOAD x3)
# = 21 measured runs. See docs/test-results/phase1/unit6-benchmark-plan.md.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

# Keep the Mac awake for the whole matrix (Unit 6 plan section 7) — caffeinate
# exits on its own once this script's PID goes away.
caffeinate -i -w $$ &

WARMUP_SEC=120
MEASUREMENT_SEC=300

run() {
  local label="$1" core="$2" max="$3" queue="$4" rejection="$5" load="$6" rate="$7" rep="$8"
  echo ""
  echo "############################################################"
  echo "# $(date -u +%Y-%m-%dT%H:%M:%SZ) ${label} ${load} rate=${rate} rep=${rep}"
  echo "############################################################"
  ./scripts/run-formal-benchmark.sh "${label}" "${core}" "${max}" "${queue}" "${rejection}" \
    "${load}" "${rate}" "${WARMUP_SEC}" "${MEASUREMENT_SEC}" "${rep}"
}

# config: label core max queue rejection
declare -a CONFIGS=(
  "B 10 10 20 ABORT"
  "E 10 50 0 ABORT"
  "A 10 10 100 CALLER_RUNS"
)

for cfg in "${CONFIGS[@]}"; do
  read -r label core max queue rejection <<< "${cfg}"

  run "${label}" "${core}" "${max}" "${queue}" "${rejection}" LOW 2 1

  for rep in 1 2 3; do
    run "${label}" "${core}" "${max}" "${queue}" "${rejection}" STRESS 5 "${rep}"
  done

  for rep in 1 2 3; do
    run "${label}" "${core}" "${max}" "${queue}" "${rejection}" OVERLOAD 12 "${rep}"
  done
done

echo ""
echo "=== Unit 6 matrix complete: $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
