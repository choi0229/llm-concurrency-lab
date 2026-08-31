#!/usr/bin/env bash
# Phase 4 Unit 3 — Direct Mock open-model (constant-arrival-rate) control calibration.
#
# k6/xk6-sse -> Mock LLM DIRECTLY (no Gateway). Same purpose/caveats as
# run-phase4-direct-mock-closed-control.sh — this is NOT a Gateway measurement.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
K6="${ROOT}/.native-runtime/k6"
MOCK_DIR="${ROOT}/mock-llm-fastapi"
SCENARIO="${ROOT}/load-test-k6/scenarios/03-constant-arrival-rate-continuous.js"
OUT_ROOT="${ROOT}/docs/test-results/phase4/unit3-control-calibration/open"
MOCK_URL="http://127.0.0.1:8000"
MOCK_PORT=8000

FIRST_CHUNK_DELAY_MS=1000
CHUNK_INTERVAL_MS=200
CHUNK_COUNT=35

# Calibration-only duration (NOT Formal) — short window, frozen for every rate this Unit
# (docs/test-plan/phase4-design.md Unit 3 brief section 16).
WARMUP_SEC=15
MEASUREMENT_SEC=30

RATES=(10 20 40 80 160 320)

mkdir -p "$OUT_ROOT"

log() { echo "[$(date '+%H:%M:%S')] $*"; }
port_free() { ! lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

start_mock() {
  if ! port_free "$MOCK_PORT"; then log "FATAL: port $MOCK_PORT not free"; exit 1; fi
  cd "$MOCK_DIR"
  MOCK_LLM_MAX_CONCURRENT_PROCESSING=0 MOCK_LLM_MAX_WAITING=0 \
    nohup .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$MOCK_PORT" \
    > /tmp/phase4-unit3-mock-open.log 2>&1 &
  echo $! > /tmp/phase4-unit3-mock-open.pid
  cd "$ROOT"
  for _ in $(seq 1 30); do
    curl -sf "$MOCK_URL/healthz" >/dev/null 2>&1 && return 0
    sleep 0.5
  done
  log "FATAL: Mock did not become healthy"; exit 1
}

stop_mock() {
  if [ -f /tmp/phase4-unit3-mock-open.pid ]; then
    kill "$(cat /tmp/phase4-unit3-mock-open.pid)" 2>/dev/null
    for _ in $(seq 1 20); do port_free "$MOCK_PORT" && break; sleep 0.5; done
    rm -f /tmp/phase4-unit3-mock-open.pid
  fi
}

sample_loop() {
  local pid=$1 outfile=$2
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

for R in "${RATES[@]}"; do
  log "=== Open control R=${R} req/s ==="
  LEVEL_DIR="$OUT_ROOT/r${R}"
  mkdir -p "$LEVEL_DIR"

  if ! memory_pressure_ok; then
    STOP_REASON="HOST_MEMORY_LIMIT (pressure not normal before R=$R)"
    log "HARD STOP: $STOP_REASON"; break
  fi

  start_mock
  MOCK_PID=$(cat /tmp/phase4-unit3-mock-open.pid)
  curl -sS "$MOCK_URL/metrics" > "$LEVEL_DIR/mock-metrics-before.txt"

  sample_loop "$MOCK_PID" "$LEVEL_DIR/mock-resource-samples.csv" &
  MOCK_SAMPLER_PID=$!

  # preAllocatedVUs/maxVUs sized generously above the Little's-Law sanity estimate (R * ~7.8s)
  # so k6 itself never becomes the bottleneck at these rates.
  MAX_VUS=$(( R * 20 + 200 ))
  PRE_VUS=$(( R * 10 + 50 ))

  K6_START=$(date +%s)
  "$K6" run \
    -e RATE="$R" \
    -e WARMUP_SEC=$WARMUP_SEC \
    -e MEASUREMENT_SEC=$MEASUREMENT_SEC \
    -e PRE_ALLOCATED_VUS="$PRE_VUS" \
    -e MAX_VUS="$MAX_VUS" \
    -e GATEWAY_URL="${MOCK_URL}/mock/stream" \
    -e FIRST_CHUNK_DELAY_MS=$FIRST_CHUNK_DELAY_MS \
    -e CHUNK_INTERVAL_MS=$CHUNK_INTERVAL_MS \
    -e CHUNK_COUNT=$CHUNK_COUNT \
    --summary-export "$LEVEL_DIR/k6-summary.json" \
    "$SCENARIO" > "$LEVEL_DIR/k6-stdout.log" 2>&1 &
  K6_PID=$!
  sample_loop "$K6_PID" "$LEVEL_DIR/k6-resource-samples.csv" &
  K6_SAMPLER_PID=$!
  wait "$K6_PID"
  K6_EXIT=$?
  K6_ELAPSED=$(( $(date +%s) - K6_START ))
  echo "$K6_EXIT" > "$LEVEL_DIR/k6-exit-code.txt"
  kill "$K6_SAMPLER_PID" 2>/dev/null
  wait "$K6_SAMPLER_PID" 2>/dev/null

  kill "$MOCK_SAMPLER_PID" 2>/dev/null
  wait "$MOCK_SAMPLER_PID" 2>/dev/null

  curl -sS "$MOCK_URL/metrics" > "$LEVEL_DIR/mock-metrics-after.txt"
  cp /tmp/phase4-unit3-mock-open.log "$LEVEL_DIR/mock.log"

  STARTED=$(jq -r '.metrics.measurement_iterations_started_total.count // 0' "$LEVEL_DIR/k6-summary.json" 2>/dev/null || echo 0)
  COMPLETED=$(jq -r '.metrics.client_completed_total.count // 0' "$LEVEL_DIR/k6-summary.json" 2>/dev/null || echo 0)
  REJECTED=$(jq -r '.metrics.client_rejected_total.count // 0' "$LEVEL_DIR/k6-summary.json" 2>/dev/null || echo 0)
  FAILED_MID=$(jq -r '.metrics.client_failed_mid_stream_total.count // 0' "$LEVEL_DIR/k6-summary.json" 2>/dev/null || echo 0)
  FAILED_NOEVT=$(jq -r '.metrics.client_failed_no_event_total.count // 0' "$LEVEL_DIR/k6-summary.json" 2>/dev/null || echo 0)
  DROPPED=$(jq -r '.metrics.dropped_iterations.count // 0' "$LEVEL_DIR/k6-summary.json" 2>/dev/null || echo 0)
  TTFC_P95=$(jq -r '.metrics.client_ttfc_completed_seconds."p(95)" // -1' "$LEVEL_DIR/k6-summary.json" 2>/dev/null || echo -1)
  DUR_P95=$(jq -r '.metrics.client_stream_duration_seconds."p(95)" // -1' "$LEVEL_DIR/k6-summary.json" 2>/dev/null || echo -1)
  ACTUAL_RATE=$(awk "BEGIN{ if ($MEASUREMENT_SEC>0) printf \"%.2f\", $STARTED/$MEASUREMENT_SEC; else print -1 }")

  cat > "$LEVEL_DIR/validity.json" <<EOF
{
  "target_rate": $R,
  "measurement_sec": $MEASUREMENT_SEC,
  "started": $STARTED,
  "actual_started_rate": $ACTUAL_RATE,
  "completed": $COMPLETED,
  "rejected": $REJECTED,
  "failed_mid_stream": $FAILED_MID,
  "failed_no_event": $FAILED_NOEVT,
  "dropped_iterations": $DROPPED,
  "ttfc_p95_completed_seconds": $TTFC_P95,
  "stream_duration_p95_seconds": $DUR_P95,
  "k6_exit_code": $K6_EXIT,
  "k6_elapsed_seconds": $K6_ELAPSED
}
EOF
  log "R=$R started=$STARTED actual_rate=${ACTUAL_RATE}/s completed=$COMPLETED rejected=$REJECTED failed_mid=$FAILED_MID failed_noevt=$FAILED_NOEVT dropped=$DROPPED"

  stop_mock

  VALID=1
  [ "$K6_EXIT" != "0" ] && VALID=0
  [ "$DROPPED" != "0" ] && [ "$DROPPED" != "null" ] && VALID=0
  [ "$FAILED_MID" != "0" ] && [ "$FAILED_MID" != "null" ] && VALID=0
  [ "$FAILED_NOEVT" != "0" ] && [ "$FAILED_NOEVT" != "null" ] && VALID=0
  [ "$REJECTED" != "0" ] && [ "$REJECTED" != "null" ] && VALID=0
  # actual_started_rate must be reasonably close to target (>=95%)
  CLOSE=$(awk "BEGIN{ print ($ACTUAL_RATE >= $R*0.95) ? 1 : 0 }")
  [ "$CLOSE" != "1" ] && VALID=0

  if [ "$VALID" = "1" ]; then
    LAST_CLEAN=$R
    log "R=$R: CLEAN"
  else
    STOP_REASON="first non-clean rate at R=$R (see validity.json / k6-stdout.log / mock.log)"
    log "R=$R: NOT CLEAN — stopping"
    break
  fi

  log "cooldown 5s before next rate"
  sleep 5
done

stop_mock 2>/dev/null

echo
echo "=== Open control calibration finished ==="
echo "Last clean R: $LAST_CLEAN req/s"
echo "Stop reason: ${STOP_REASON:-completed all rates clean}"
echo "$LAST_CLEAN" > "$OUT_ROOT/last-clean-r.txt"
echo "${STOP_REASON:-completed all rates clean}" > "$OUT_ROOT/stop-reason.txt"
