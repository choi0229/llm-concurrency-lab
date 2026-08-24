#!/usr/bin/env bash
# Phase 3 Unit 6.5/7 -- Formal 27-run matrix driver (docs/test-plan/phase3-formal-protocol.md
# Sec 4). Run order is a LITERAL array, not computed by a rotate-at-runtime algorithm, so
# execution order cannot silently drift from what Sec 4 froze.
#
# DO NOT RUN until: (1) the Canary (scripts/run-phase3-native-formal-benchmark.sh A r3 3 0 canary)
# has PASSed per protocol Sec 29, AND (2) a separate, explicit user approval has been given for
# the full 27-run matrix specifically (protocol Sec 14/44 -- Unit 6.5 froze the protocol and wrote
# this script but did not execute it).
set -uo pipefail  # not -e: a run's non-zero exit must not abort the rest of the matrix

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# config load rate repeat  (LABEL defaults to run<repeat> inside the benchmark script)
RUNS=(
  # --- Repeat 1 (A,B,C) ---
  "A r3 3 1"   "B r3 3 1"   "C r3 3 1"
  "A r7 7 1"   "B r7 7 1"   "C r7 7 1"
  "A r10 10 1" "B r10 10 1" "C r10 10 1"
  # --- Repeat 2 (B,C,A) ---
  "B r3 3 2"   "C r3 3 2"   "A r3 3 2"
  "B r7 7 2"   "C r7 7 2"   "A r7 7 2"
  "B r10 10 2" "C r10 10 2" "A r10 10 2"
  # --- Repeat 3 (C,A,B) ---
  "C r3 3 3"   "A r3 3 3"   "B r3 3 3"
  "C r7 7 3"   "A r7 7 3"   "B r7 7 3"
  "C r10 10 3" "A r10 10 3" "B r10 10 3"
)

echo "[matrix] ${#RUNS[@]} runs frozen (expect 27)"
[ "${#RUNS[@]}" -eq 27 ] || { echo "[matrix] FATAL: run count != 27, refusing to start" >&2; exit 1; }

FAIL_COUNT=0
for spec in "${RUNS[@]}"; do
  read -r cfg load rate repeat <<< "$spec"
  echo "[matrix] === ${cfg} ${load} rate=${rate} repeat=${repeat} ==="
  if ! ./scripts/run-phase3-native-formal-benchmark.sh "$cfg" "$load" "$rate" "$repeat"; then
    echo "[matrix] WARNING: run ${cfg}-${load}-run${repeat} exited non-zero -- see its result.json" >&2
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
done

echo "[matrix] done. ${FAIL_COUNT} run(s) reported non-zero exit -- check each result.json's"
echo "[matrix] 'valid'/'invalid_reason' field individually (a non-zero exit does not by itself"
echo "[matrix] mean invalid, and 'valid: true' does not require a zero exit -- Sec 13-C policy)."
echo "[matrix] Invalid runs must be retried in a fresh environment per protocol Sec 13-C before"
echo "[matrix] this matrix is considered complete; retries are NOT automated by this script."
