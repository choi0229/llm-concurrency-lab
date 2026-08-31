#!/usr/bin/env bash
# Phase 4.1 (Unit 6.6/6.7) — Extended Closed-model harness. Byte-identical to
# scripts/run-phase4-closed-benchmark.sh (Unit 4/5/6's frozen harness, left completely untouched)
# EXCEPT: (1) M3's WEBCLIENT_MAX_CONNECTIONS/WEBCLIENT_PENDING_ACQUIRE_MAX_COUNT are the
# Phase 4.1 re-frozen values (docs/test-results/phase4/unit6.5-extended-closed-control/SUMMARY.md
# -- 1400, per docs/decisions/phase4-scalability-definition.md section 16-5's required
# "new control range -> headroom policy 재계산 -> M3 maxConnections 재-freeze" sequence), and
# (2) its own OUT_DIR base so Extended artifacts never land in unit4-closed-harness/ alongside the
# original Closed Formal evidence. Workload, SLO, collector invocation, and every other measurement
# semantic are identical.
#
# Usage: run-phase4-closed-benchmark-extended.sh <model:m1|m2|m3> <concurrency> <run_label>
set -uo pipefail

MODEL="${1:?model required: m1|m2|m3}"
N="${2:?concurrency required}"
RUN_LABEL="${3:?run_label required}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JAVA_HOME="${ROOT}/.native-runtime/jdk-21.0.11+10/Contents/Home"
K6="${ROOT}/.native-runtime/k6"
PROMETHEUS_BIN="${ROOT}/.native-runtime/prometheus-3.13.2.darwin-arm64/prometheus"
PROM_CONFIG="${ROOT}/monitoring/prometheus/prometheus-phase4.yml"
SCENARIO="${ROOT}/load-test-k6/scenarios/04-phase4-closed-wave.js"
MOCK_DIR="${ROOT}/mock-llm-fastapi"

MOCK_PORT=8000
PROM_PORT=9094
PREWARM_COUNT=5
CHAT_TOTAL_TIMEOUT_MS=60000
FIRST_CHUNK_DELAY_MS=1000
CHUNK_INTERVAL_MS=200
CHUNK_COUNT=35

# Phase 4.1 re-freeze (unit6.5-extended-closed-control/SUMMARY.md) -- SAFE_CLOSED_MAX_EXTENDED=1120,
# headroom 1.25x -> 1400. NOT the Unit 5/6 value (800) -- this script is never used for N<=640
# Closed Formal work, only for Extended Closed screening/Formal at N>640.
EXTENDED_M3_MAX_CONNECTIONS=1400
EXTENDED_M3_PENDING_ACQUIRE_MAX_COUNT=1400

case "$MODEL" in
  m1) GW_DIR="gateway-phase4-platform-queue"; GW_JAR_GLOB="gateway-phase4-platform-queue-0.1.0.jar"; GW_PORT=18101; PROM_JOB_MODEL="m1-platform-queue" ;;
  m2) GW_DIR="gateway-phase4-virtual-thread";  GW_JAR_GLOB="gateway-phase4-virtual-thread-0.1.0.jar";  GW_PORT=18102; PROM_JOB_MODEL="m2-virtual-thread" ;;
  m3) GW_DIR="gateway-phase4-webflux";         GW_JAR_GLOB="gateway-phase4-webflux-0.1.0.jar";         GW_PORT=18103; PROM_JOB_MODEL="m3-webflux" ;;
  *) echo "FATAL: unknown model '$MODEL' (expected m1|m2|m3)" >&2; exit 1 ;;
esac

OUT_DIR="${ROOT}/docs/test-results/phase4/unit4.5-extended-closed-harness/${MODEL}-n${N}-${RUN_LABEL}"
mkdir -p "$OUT_DIR"

log() { echo "[$(date '+%H:%M:%S')] $*"; }
port_free() { ! lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }
now_ms() { python3 -c 'import time; print(int(time.time()*1000))'; }

FAIL_REASON=""
fail() { FAIL_REASON="$1"; log "FATAL: $1"; }

# ---- Preflight ----
log "=== Preflight: $MODEL N=$N ($RUN_LABEL) [EXTENDED] ==="
for p in "$MOCK_PORT" "$GW_PORT" "$PROM_PORT"; do
  if ! port_free "$p"; then fail "port $p not free before start"; fi
done
if [ -n "$FAIL_REASON" ]; then echo "$FAIL_REASON" > "$OUT_DIR/postflight.txt"; exit 1; fi

# ---- Start Mock ----
cd "$MOCK_DIR"
MOCK_LLM_MAX_CONCURRENT_PROCESSING=0 MOCK_LLM_MAX_WAITING=0 \
  nohup .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$MOCK_PORT" \
  > "$OUT_DIR/mock.log" 2>&1 &
MOCK_PID=$!
cd "$ROOT"
for _ in $(seq 1 30); do curl -sf "http://127.0.0.1:${MOCK_PORT}/healthz" >/dev/null 2>&1 && break; sleep 0.5; done
if ! curl -sf "http://127.0.0.1:${MOCK_PORT}/healthz" >/dev/null 2>&1; then fail "Mock did not become healthy"; fi

# ---- Start Gateway ----
GW_JAR="${ROOT}/${GW_DIR}/build/libs/${GW_JAR_GLOB}"
if [ ! -f "$GW_JAR" ]; then fail "Gateway jar not found: $GW_JAR"; fi

PROCESS_START_MS=$(now_ms)
cd "${ROOT}/${GW_DIR}"
if [ "$MODEL" = "m1" ]; then
  PT_WORKER_COUNT=50 PT_QUEUE_CAPACITY=500 \
  SERVLET_WRITE_POOL_SIZE=64 SERVLET_WRITE_QUEUE_CAPACITY=20000 SERVLET_PER_STREAM_BUFFER_CAPACITY=8 \
  CHAT_TOTAL_TIMEOUT_MS=$CHAT_TOTAL_TIMEOUT_MS MOCK_LLM_BASE_URL="http://127.0.0.1:${MOCK_PORT}" SERVER_PORT=$GW_PORT \
    nohup "${JAVA_HOME}/bin/java" -jar "$GW_JAR" > "$OUT_DIR/gateway.log" 2>&1 &
elif [ "$MODEL" = "m2" ]; then
  SERVLET_WRITE_POOL_SIZE=64 SERVLET_WRITE_QUEUE_CAPACITY=20000 SERVLET_PER_STREAM_BUFFER_CAPACITY=8 \
  CHAT_TOTAL_TIMEOUT_MS=$CHAT_TOTAL_TIMEOUT_MS MOCK_LLM_BASE_URL="http://127.0.0.1:${MOCK_PORT}" SERVER_PORT=$GW_PORT \
    nohup "${JAVA_HOME}/bin/java" -jar "$GW_JAR" > "$OUT_DIR/gateway.log" 2>&1 &
else
  WEBCLIENT_MAX_CONNECTIONS=$EXTENDED_M3_MAX_CONNECTIONS WEBCLIENT_PENDING_ACQUIRE_MAX_COUNT=$EXTENDED_M3_PENDING_ACQUIRE_MAX_COUNT \
  CHAT_TOTAL_TIMEOUT_MS=$CHAT_TOTAL_TIMEOUT_MS MOCK_LLM_BASE_URL="http://127.0.0.1:${MOCK_PORT}" SERVER_PORT=$GW_PORT \
    nohup "${JAVA_HOME}/bin/java" -jar "$GW_JAR" > "$OUT_DIR/gateway.log" 2>&1 &
fi
GW_PID=$!
cd "$ROOT"

for _ in $(seq 1 40); do curl -sf "http://127.0.0.1:${GW_PORT}/healthz" >/dev/null 2>&1 && break; sleep 0.5; done
if ! curl -sf "http://127.0.0.1:${GW_PORT}/healthz" >/dev/null 2>&1; then fail "Gateway did not become healthy"; fi

# ---- PID / command verification ----
ACTUAL_CMD=$(ps -o command= -p "$GW_PID" 2>/dev/null)
{
  echo "GATEWAY_PID=$GW_PID"
  echo "expected_jar=$GW_JAR_GLOB"
  echo "actual_command=$ACTUAL_CMD"
  if echo "$ACTUAL_CMD" | grep -q "$GW_JAR_GLOB"; then
    echo "PID_MATCH=true"
  else
    echo "PID_MATCH=false"
    fail "Gateway PID $GW_PID command does not contain expected jar name $GW_JAR_GLOB"
  fi
} > "$OUT_DIR/pid-verification.txt"

# ---- Start Prometheus (fresh TSDB) ----
PROM_TSDB=$(mktemp -d)
nohup "$PROMETHEUS_BIN" --config.file="$PROM_CONFIG" --storage.tsdb.path="$PROM_TSDB" \
  --web.listen-address="127.0.0.1:${PROM_PORT}" --log.level=warn \
  > "$OUT_DIR/prometheus.log" 2>&1 &
PROM_PID=$!
for _ in $(seq 1 30); do curl -sf "http://127.0.0.1:${PROM_PORT}/-/ready" >/dev/null 2>&1 && break; sleep 0.5; done

# ---- RSS/CPU sampler on the Gateway PID ----
echo "epoch_s,rss_kib,pcpu" > "$OUT_DIR/rss-samples.csv"
( while kill -0 "$GW_PID" 2>/dev/null; do
    rss=$(ps -o rss= -p "$GW_PID" 2>/dev/null | tr -d ' ')
    cpu=$(ps -o %cpu= -p "$GW_PID" 2>/dev/null | tr -d ' ')
    echo "$(date +%s),${rss:-0},${cpu:-0}" >> "$OUT_DIR/rss-samples.csv"
    sleep 1
  done ) &
SAMPLER_PID=$!

if [ -n "$FAIL_REASON" ]; then
  echo "$FAIL_REASON" > "$OUT_DIR/postflight.txt"
  kill "$SAMPLER_PID" "$PROM_PID" "$GW_PID" "$MOCK_PID" 2>/dev/null
  exit 1
fi

# ---- Pre-wave Gateway metrics snapshot (BEFORE prewarm, i.e. before k6 starts at all) ----
curl -sS "http://127.0.0.1:${GW_PORT}/actuator/prometheus" > "$OUT_DIR/gateway-metrics-before-wave.txt"

# ---- k6 closed wave (includes internal sequential prewarm via setup()) ----
K6_WALL_START_MS=$(now_ms)
"$K6" run \
  -e VUS="$N" \
  -e PREWARM_COUNT=$PREWARM_COUNT \
  -e GATEWAY_URL="http://127.0.0.1:${GW_PORT}/chat/stream" \
  -e FIRST_CHUNK_DELAY_MS=$FIRST_CHUNK_DELAY_MS \
  -e CHUNK_INTERVAL_MS=$CHUNK_INTERVAL_MS \
  -e CHUNK_COUNT=$CHUNK_COUNT \
  -e MAX_DURATION=120s \
  --summary-export "$OUT_DIR/k6-summary.json" \
  "$SCENARIO" > "$OUT_DIR/k6-stdout.log" 2>&1
K6_EXIT=$?
K6_WALL_END_MS=$(now_ms)
echo "$K6_EXIT" > "$OUT_DIR/k6-exit-code.txt"

# ---- Drain ----
DRAIN_START_MS=$(now_ms)
for _ in $(seq 1 60); do
  ACTIVE=$(curl -sS "http://127.0.0.1:${GW_PORT}/actuator/prometheus" 2>/dev/null | awk '/^gateway_active_requests /{print $2}')
  if [ "${ACTIVE:-1}" = "0" ] || [ "${ACTIVE:-1}" = "0.0" ]; then break; fi
  sleep 0.5
done
DRAIN_END_MS=$(now_ms)

# ---- Snapshot metrics/state before teardown ----
curl -sS "http://127.0.0.1:${GW_PORT}/actuator/prometheus" > "$OUT_DIR/gateway-metrics-final.txt"
curl -sS "http://127.0.0.1:${MOCK_PORT}/metrics" > "$OUT_DIR/mock-metrics-final.txt"

# Prometheus range query covering the whole run window, generous margin.
Q_START=$(( (PROCESS_START_MS/1000) - 2 ))
Q_END=$(( (DRAIN_END_MS/1000) + 2 ))
for metric in gateway_active_requests jvm_threads_live_threads process_cpu_usage system_cpu_count \
              process_files_open_files process_files_max_files jvm_memory_used_bytes \
              executor_active executor_queue_depth executor_rejected_total \
              virtual_tasks_active virtual_tasks_started_total \
              reactor_netty_connection_provider_active_connections \
              reactor_netty_connection_provider_pending_connections; do
  curl -sS -G "http://127.0.0.1:${PROM_PORT}/api/v1/query_range" \
    --data-urlencode "query=${metric}{model=\"${PROM_JOB_MODEL}\"}" \
    --data-urlencode "start=${Q_START}" --data-urlencode "end=${Q_END}" --data-urlencode "step=1" \
    > "$OUT_DIR/prom-${metric}.json" 2>/dev/null
done

# ---- Postflight ----
{
  echo "gateway_active_requests_final=$(awk '/^gateway_active_requests /{print $2}' "$OUT_DIR/gateway-metrics-final.txt")"
  echo "gateway_upstream_active_final=$(awk '/^gateway_upstream_active /{print $2}' "$OUT_DIR/gateway-metrics-final.txt")"
  if [ "$MODEL" = "m1" ]; then
    echo "executor_active_final=$(awk '/^executor_active /{print $2}' "$OUT_DIR/gateway-metrics-final.txt")"
    echo "executor_queue_depth_final=$(awk '/^executor_queue_depth /{print $2}' "$OUT_DIR/gateway-metrics-final.txt")"
    echo "servlet_write_executor_active_final=$(awk '/^servlet_write_executor_active /{print $2}' "$OUT_DIR/gateway-metrics-final.txt")"
    echo "servlet_write_executor_queue_depth_final=$(awk '/^servlet_write_executor_queue_depth /{print $2}' "$OUT_DIR/gateway-metrics-final.txt")"
    echo "servlet_write_stream_buffered_frames_final=$(awk '/^servlet_write_stream_buffered_frames /{print $2}' "$OUT_DIR/gateway-metrics-final.txt")"
  elif [ "$MODEL" = "m2" ]; then
    echo "virtual_tasks_active_final=$(awk '/^virtual_tasks_active /{print $2}' "$OUT_DIR/gateway-metrics-final.txt")"
    echo "servlet_write_executor_active_final=$(awk '/^servlet_write_executor_active /{print $2}' "$OUT_DIR/gateway-metrics-final.txt")"
    echo "servlet_write_executor_queue_depth_final=$(awk '/^servlet_write_executor_queue_depth /{print $2}' "$OUT_DIR/gateway-metrics-final.txt")"
  else
    echo "reactor_netty_pending_final=$(awk '/^reactor_netty_connection_provider_pending_connections/{print $2}' "$OUT_DIR/gateway-metrics-final.txt" | head -1)"
  fi
  echo "mock_active_requests_final=$(awk '/^mockllm_active_requests /{print $2}' "$OUT_DIR/mock-metrics-final.txt")"
  echo "mock_waiting_requests_final=$(awk '/^mockllm_waiting_requests /{print $2}' "$OUT_DIR/mock-metrics-final.txt")"
} > "$OUT_DIR/postflight.txt"

# ---- Environment evidence ----
cat > "$OUT_DIR/environment.json" <<EOF
{
  "model": "$MODEL",
  "concurrency": $N,
  "run_label": "$RUN_LABEL",
  "gateway_port": $GW_PORT,
  "mock_port": $MOCK_PORT,
  "prometheus_port": $PROM_PORT,
  "prometheus_scrape_interval": "1s",
  "prewarm_count": $PREWARM_COUNT,
  "chat_total_timeout_ms": $CHAT_TOTAL_TIMEOUT_MS,
  "workload": {"firstChunkDelayMs": $FIRST_CHUNK_DELAY_MS, "chunkIntervalMs": $CHUNK_INTERVAL_MS, "chunkCount": $CHUNK_COUNT, "chunkSizeBytes": 64},
  "gateway_jar": "$GW_JAR_GLOB",
  "gateway_pid": $GW_PID,
  "jdk_home": "$JAVA_HOME",
  "extended_m3_max_connections": $EXTENDED_M3_MAX_CONNECTIONS,
  "extended_m3_pending_acquire_max_count": $EXTENDED_M3_PENDING_ACQUIRE_MAX_COUNT
}
EOF

cat > "$OUT_DIR/timestamps.json" <<EOF
{
  "process_start_ms": $PROCESS_START_MS,
  "k6_wall_start_ms": $K6_WALL_START_MS,
  "k6_wall_end_ms": $K6_WALL_END_MS,
  "drain_start_ms": $DRAIN_START_MS,
  "drain_end_ms": $DRAIN_END_MS
}
EOF

# ---- Teardown ----
kill "$SAMPLER_PID" 2>/dev/null; wait "$SAMPLER_PID" 2>/dev/null
kill "$GW_PID" 2>/dev/null
kill "$PROM_PID" 2>/dev/null
kill "$MOCK_PID" 2>/dev/null
for _ in $(seq 1 20); do
  port_free "$GW_PORT" && port_free "$MOCK_PORT" && port_free "$PROM_PORT" && break
  sleep 0.5
done
rm -rf "$PROM_TSDB"

if [ -n "$FAIL_REASON" ]; then
  echo "FAIL: $FAIL_REASON" >> "$OUT_DIR/postflight.txt"
  exit 1
fi

log "=== Done: $MODEL N=$N ($RUN_LABEL) [EXTENDED] -> $OUT_DIR ==="
