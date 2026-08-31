#!/usr/bin/env bash
# Phase 4.1 (Unit 6.5) — Extended Direct Mock closed-model control calibration.
#
# Separate from, and does not modify, scripts/run-phase4-direct-mock-closed-control.sh (the
# original Unit 3 script/output stay untouched at docs/test-results/phase4/unit3-control-calibration/).
# Same methodology verbatim (k6/xk6-sse -> Mock LLM directly, no Gateway) -- only LEVELS and
# OUT_ROOT differ, to fill the gap between Unit 3's already-established robust_clean_ceiling_N=800
# and marginal_ceiling_N=1600 (docs/test-results/phase4/unit3-control-calibration/closed-control-summary.json)
# without re-running levels Unit 3 already characterized.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
K6="${ROOT}/.native-runtime/k6"
MOCK_DIR="${ROOT}/mock-llm-fastapi"
SCENARIO="${ROOT}/load-test-k6/scenarios/01-concurrent-connections.js"
OUT_ROOT="${ROOT}/docs/test-results/phase4/unit6.5-extended-closed-control"
MOCK_URL="http://127.0.0.1:8000"
MOCK_PORT=8000

FIRST_CHUNK_DELAY_MS=1000
CHUNK_INTERVAL_MS=200
CHUNK_COUNT=35

read -ra LEVELS <<< "${LEVELS_OVERRIDE:-1000 1200 1400}"

mkdir -p "$OUT_ROOT"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

port_free() {
  ! lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

start_mock() {
  if ! port_free "$MOCK_PORT"; then
    log "FATAL: port $MOCK_PORT not free before starting Mock"; exit 1
  fi
  cd "$MOCK_DIR"
  MOCK_LLM_MAX_CONCURRENT_PROCESSING=0 MOCK_LLM_MAX_WAITING=0 \
    nohup .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$MOCK_PORT" \
    > /tmp/phase4-unit6_5-mock.log 2>&1 &
  echo $! > /tmp/phase4-unit6_5-mock.pid
  cd "$ROOT"
  for _ in $(seq 1 30); do
    if curl -sf "$MOCK_URL/healthz" >/dev/null 2>&1; then return 0; fi
    sleep 0.5
  done
  log "FATAL: Mock did not become healthy"; exit 1
}

stop_mock() {
  if [ -f /tmp/phase4-unit6_5-mock.pid ]; then
    kill "$(cat /tmp/phase4-unit6_5-mock.pid)" 2>/dev/null
    for _ in $(seq 1 20); do
      port_free "$MOCK_PORT" && break
      sleep 0.5
    done
    rm -f /tmp/phase4-unit6_5-mock.pid
  fi
}

sample_loop() {  # $1=pid $2=label $3=outfile
  local pid=$1 label=$2 outfile=$3
  echo "epoch_s,rss_kib,pcpu,fd_count" > "$outfile"
  while kill -0 "$pid" 2>/dev/null; do
    local rss cpu fd
    rss=$(ps -o rss= -p "$pid" 2>/dev/null | tr -d ' ')
    cpu=$(ps -o %cpu= -p "$pid" 2>/dev/null | tr -d ' ')
    fd=$(lsof -p "$pid" 2>/dev/null | wc -l | tr -d ' ')
    echo "$(date +%s),${rss:-0},${cpu:-0},${fd:-0}" >> "$outfile"
    sleep 1
  done
}

memory_pressure_ok() {
  local level
  level=$(sysctl -n kern.memorystatus_vm_pressure_level 2>/dev/null || echo 1)
  [ "$level" = "1" ]
}

LAST_CLEAN=0
STOP_REASON=""

for N in "${LEVELS[@]}"; do
  log "=== Extended closed control N=$N ==="
  LEVEL_DIR="$OUT_ROOT/n${N}${LABEL_SUFFIX:-}"
  mkdir -p "$LEVEL_DIR"

  if ! memory_pressure_ok; then
    STOP_REASON="HOST_MEMORY_LIMIT (pressure not normal before N=$N)"
    log "HARD STOP: $STOP_REASON"
    break
  fi

  start_mock
  MOCK_PID=$(cat /tmp/phase4-unit6_5-mock.pid)
  curl -sS "$MOCK_URL/metrics" > "$LEVEL_DIR/mock-metrics-before.txt"

  sample_loop "$MOCK_PID" mock "$LEVEL_DIR/mock-resource-samples.csv" &
  MOCK_SAMPLER_PID=$!

  K6_START=$(date +%s)
  "$K6" run \
    -e VUS="$N" \
    -e GATEWAY_URL="${MOCK_URL}/mock/stream" \
    -e FIRST_CHUNK_DELAY_MS=$FIRST_CHUNK_DELAY_MS \
    -e CHUNK_INTERVAL_MS=$CHUNK_INTERVAL_MS \
    -e CHUNK_COUNT=$CHUNK_COUNT \
    -e MAX_DURATION=120s \
    --summary-export "$LEVEL_DIR/k6-summary.json" \
    "$SCENARIO" > "$LEVEL_DIR/k6-stdout.log" 2>&1 &
  K6_PID=$!
  sample_loop "$K6_PID" k6 "$LEVEL_DIR/k6-resource-samples.csv" &
  K6_SAMPLER_PID=$!
  wait "$K6_PID"
  K6_EXIT=$?
  K6_ELAPSED=$(( $(date +%s) - K6_START ))
  echo "$K6_EXIT" > "$LEVEL_DIR/k6-exit-code.txt"

  kill "$MOCK_SAMPLER_PID" 2>/dev/null
  wait "$MOCK_SAMPLER_PID" 2>/dev/null
  kill "$K6_SAMPLER_PID" 2>/dev/null
  wait "$K6_SAMPLER_PID" 2>/dev/null

  curl -sS "$MOCK_URL/metrics" > "$LEVEL_DIR/mock-metrics-after.txt"
  cp /tmp/phase4-unit6_5-mock.log "$LEVEL_DIR/mock.log"

  COMPLETED=$(jq -r '.metrics.client_stream_completed_total.count // 0' "$LEVEL_DIR/k6-summary.json" 2>/dev/null || echo 0)
  FAILED=$(jq -r '.metrics.client_stream_failed_total.count // 0' "$LEVEL_DIR/k6-summary.json" 2>/dev/null || echo 0)
  DROPPED=$(jq -r '.metrics.dropped_iterations.count // 0' "$LEVEL_DIR/k6-summary.json" 2>/dev/null || echo 0)
  TTFC_P95=$(jq -r '.metrics.client_ttfc_seconds."p(95)" // -1' "$LEVEL_DIR/k6-summary.json" 2>/dev/null || echo -1)
  DUR_P95=$(jq -r '.metrics.client_stream_duration_seconds."p(95)" // -1' "$LEVEL_DIR/k6-summary.json" 2>/dev/null || echo -1)

  cat > "$LEVEL_DIR/validity.json" <<EOF
{
  "N": $N,
  "k6_exit_code": $K6_EXIT,
  "k6_elapsed_seconds": $K6_ELAPSED,
  "completed": $COMPLETED,
  "failed": $FAILED,
  "dropped_iterations": $DROPPED,
  "ttfc_p95_seconds": $TTFC_P95,
  "stream_duration_p95_seconds": $DUR_P95
}
EOF
  log "N=$N completed=$COMPLETED failed=$FAILED dropped=$DROPPED ttfc_p95=${TTFC_P95}s dur_p95=${DUR_P95}s k6_exit=$K6_EXIT"

  stop_mock

  VALID=1
  if [ "$K6_EXIT" != "0" ]; then VALID=0; fi
  if [ "$FAILED" != "0" ] && [ "$FAILED" != "null" ]; then VALID=0; fi
  if [ "$DROPPED" != "0" ] && [ "$DROPPED" != "null" ]; then VALID=0; fi
  if [ "$COMPLETED" != "$N" ]; then VALID=0; fi

  DEGRADED=$(awk "BEGIN{ print (($TTFC_P95 > 3.0) || ($DUR_P95 > 11.85)) ? 1 : 0 }")
  if [ "$DEGRADED" = "1" ]; then
    log "N=$N: latency DEGRADED (ttfc_p95=${TTFC_P95}s dur_p95=${DUR_P95}s vs ~1.0s/~7.9s baseline) — not a hard failure, flagged for review"
  fi
  echo "$DEGRADED" >> "$LEVEL_DIR/validity.json.degraded_flag"

  if [ "$VALID" = "1" ]; then
    LAST_CLEAN=$N
    log "N=$N: CLEAN (degraded=$DEGRADED)"
  else
    STOP_REASON="first non-clean level at N=$N (see validity.json / k6-stdout.log / mock.log)"
    log "N=$N: NOT CLEAN — stopping (no auto-doubling past a failure)"
    break
  fi

  log "cooldown 5s before next level"
  sleep 5
done

stop_mock 2>/dev/null

echo
echo "=== Extended closed control calibration finished ==="
echo "Last clean N: $LAST_CLEAN"
echo "Stop reason: ${STOP_REASON:-completed all levels clean}"
echo "$LAST_CLEAN" > "$OUT_ROOT/last-clean-n${LABEL_SUFFIX:-}.txt"
echo "${STOP_REASON:-completed all levels clean}" > "$OUT_ROOT/stop-reason${LABEL_SUFFIX:-}.txt"
