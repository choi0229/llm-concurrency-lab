#!/usr/bin/env bash
# Phase 2 Unit 6 Formal Benchmark — full matrix driver.
#
# - Full 18-run matrix: P-E x {R3,R6,R8} x 3 reps + VT-Limited x {R3,R6,R8} x 3 reps.
# - Official Formal valid dataset starts at 0/18 (see
#   docs/test-results/phase2/unit6/README.md).
# - The "P-E R3 Stability Canary" is NOT part of this 18-run matrix.
# - Run this script only after the Stability Canary has passed.
#
# Fixed order (grouped by config then load) — allowed per docs/test-plan/
# phase2-formal-protocol.md ("단순 고정 순서도 허용한다"). Each run is fully
# independent (fresh stack every time).
set -uo pipefail  # NOT -e: one run failing should not silently kill the rest;
                   # each run's own exit status is logged and matrix continues.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

# config_label thread_mode load rate warmup_s measurement_s run_number
RUNS=(
  "pe PLATFORM r3 3 120 300 1"
  "pe PLATFORM r3 3 120 300 2"
  "pe PLATFORM r3 3 120 300 3"
  "pe PLATFORM r6 6 120 300 1"
  "pe PLATFORM r6 6 120 300 2"
  "pe PLATFORM r6 6 120 300 3"
  "pe PLATFORM r8 8 120 300 1"
  "pe PLATFORM r8 8 120 300 2"
  "pe PLATFORM r8 8 120 300 3"
  "vtl VIRTUAL_LIMITED r3 3 120 300 1"
  "vtl VIRTUAL_LIMITED r3 3 120 300 2"
  "vtl VIRTUAL_LIMITED r3 3 120 300 3"
  "vtl VIRTUAL_LIMITED r6 6 120 300 1"
  "vtl VIRTUAL_LIMITED r6 6 120 300 2"
  "vtl VIRTUAL_LIMITED r6 6 120 300 3"
  "vtl VIRTUAL_LIMITED r8 8 120 300 1"
  "vtl VIRTUAL_LIMITED r8 8 120 300 2"
  "vtl VIRTUAL_LIMITED r8 8 120 300 3"
)

TOTAL=${#RUNS[@]}
I=0
for spec in "${RUNS[@]}"; do
  I=$((I+1))
  echo "##### MATRIX [$I/$TOTAL]: ${spec} #####"
  # shellcheck disable=SC2086
  ./scripts/run-phase2-formal-benchmark.sh ${spec} 100
  RC=$?
  if [ $RC -ne 0 ]; then
    echo "##### MATRIX [$I/$TOTAL] FAILED (exit ${RC}): ${spec} #####"
  else
    echo "##### MATRIX [$I/$TOTAL] OK: ${spec} #####"
  fi
done
echo "##### MATRIX ALL DONE #####"
