#!/usr/bin/env bash
# Phase 4 Part B -- Open-model (constant-arrival-rate) harness. Same per-run lifecycle shape as
# scripts/run-phase4-closed-benchmark.sh (fresh Gateway + fresh Mock + fresh Prometheus + k6 +
# resource sampler + drain + collector + validity gate + cleanup), adapted for a single continuous
# warmup->measurement k6 process (load-test-k6/scenarios/05-phase4-open-arrival-rate.js) instead of
# a discrete closed wave. Frozen windows: docs/test-plan/phase4-open-screening-protocol.md section
# 2.1 (screening 60s/120s, formal 120s/300s, passed in via WARMUP_SEC/MEASUREMENT_SEC).
#
# Usage: run-phase4-open-benchmark.sh <model:m1|m2|m3> <rate> <run_label> <warmup_sec> <measurement_sec>
set -uo pipefail

MODEL="${1:?model required: m1|m2|m3}"
RATE="${2:?arrival rate required}"
RUN_LABEL="${3:?run_label required}"
WARMUP_SEC="${4:?warmup_sec required}"
MEASUREMENT_SEC="${5:?measurement_sec required}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JAVA_HOME="${ROOT}/.native-runtime/jdk-21.0.11+10/Contents/Home"
K6="${ROOT}/.native-runtime/k6"
PROMETHEUS_BIN="${ROOT}/.native-runtime/prometheus-3.13.2.darwin-arm64/prometheus"
PROM_CONFIG="${ROOT}/monitoring/prometheus/prometheus-phase4.yml"
SCENARIO="${ROOT}/load-test-k6/scenarios/05-phase4-open-arrival-rate.js"
MOCK_DIR="${ROOT}/mock-llm-fastapi"

MOCK_PORT=8000
PROM_PORT=9094
CHAT_TOTAL_TIMEOUT_MS=60000
FIRST_CHUNK_DELAY_MS=1000
CHUNK_INTERVAL_MS=200
CHUNK_COUNT=35

# Phase 4 Open pool re-freeze (docs/test-plan/phase4-open-screening-protocol.md section 2.2):
# ceil(SAFE_OPEN_MAX=256 * SLO_duration_s=10.0 * headroom=1.25) = 3200.
OPEN_M3_MAX_CONNECTIONS=3200
OPEN_M3_PENDING_ACQUIRE_MAX_COUNT=3200

# Phase 4 Open Unit 7.3->7.5 (docs/decisions/phase4-open-tomcat-connector-headroom.md): M1/M2 both
# run on Tomcat with an unexamined Spring Boot default (maxConnections=8192) that was found to bind
# under sustained Open-model arrival at R=160. Unit 7.3's first formula (9600, stream+keepalive
# budget) was regression-tested and found still insufficient; Unit 7.4 root-caused the true cause to
# a loadgen-side (xk6-sse) connection-lifecycle defect, not a Gateway/Tomcat sizing problem; Unit 7.5
# fixed that defect (explicit client.close() after each iteration's stream reaches a terminal state)
# and re-measured: the organic Tomcat connection population at R=160 dropped from ~10549 (broken) to
# ~1276 (fixed) -- matching Little's Law on stream duration ALONE (160*8s~=1280), confirming that
# post-stream keep-alive retention is now negligible once the client properly closes idle
# connections instead of leaking them for the server to reclaim. Final formula, mirroring M3's own
# Open pool formula now that the extra keep-alive term is no longer needed:
# ceil(SAFE_OPEN_MAX=256 * STREAM_DURATION_SLO_S=10.0 * 1.25) = 3200. Applied identically to M1 and
# M2 for cross-model fairness (ADR section 5). maxThreads and acceptCount remain unchanged (no
# binding evidence for either -- ADR section 2; reaffirmed by Unit 7.4's SYN_SENT-collapse finding).
OPEN_TOMCAT_MAX_CONNECTIONS=3200

case "$MODEL" in
  m1) GW_DIR="gateway-phase4-platform-queue"; GW_JAR_GLOB="gateway-phase4-platform-queue-0.1.0.jar"; GW_PORT=18101; PROM_JOB_MODEL="m1-platform-queue" ;;
  m2) GW_DIR="gateway-phase4-virtual-thread";  GW_JAR_GLOB="gateway-phase4-virtual-thread-0.1.0.jar";  GW_PORT=18102; PROM_JOB_MODEL="m2-virtual-thread" ;;
  m3) GW_DIR="gateway-phase4-webflux";         GW_JAR_GLOB="gateway-phase4-webflux-0.1.0.jar";         GW_PORT=18103; PROM_JOB_MODEL="m3-webflux" ;;
  *) echo "FATAL: unknown model '$MODEL' (expected m1|m2|m3)" >&2; exit 1 ;;
esac

# Phase 4 Open Unit 8: canonical Screening staging root moved to unit8-open-harness/ to match
# scripts/run_phase4_open_screening.py's HARNESS_OUT_ROOT under the open-final-client-lifecycle-v1
# epoch -- Unit 7's unit7-open-harness/ stays as history, not written to by this script anymore.
OUT_DIR="${ROOT}/docs/test-results/phase4/unit8-open-harness/${MODEL}-r${RATE}-${RUN_LABEL}"
mkdir -p "$OUT_DIR"

log() { echo "[$(date '+%H:%M:%S')] $*"; }
port_free() { ! lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }
now_ms() { python3 -c 'import time; print(int(time.time()*1000))'; }

FAIL_REASON=""
fail() { FAIL_REASON="$1"; log "FATAL: $1"; }

log "=== Preflight: $MODEL R=$RATE ($RUN_LABEL) warmup=${WARMUP_SEC}s measurement=${MEASUREMENT_SEC}s ==="
for p in "$MOCK_PORT" "$GW_PORT" "$PROM_PORT"; do
  if ! port_free "$p"; then fail "port $p not free before start"; fi
done
if [ -n "$FAIL_REASON" ]; then echo "$FAIL_REASON" > "$OUT_DIR/postflight.txt"; exit 1; fi

cd "$MOCK_DIR"
MOCK_LLM_MAX_CONCURRENT_PROCESSING=0 MOCK_LLM_MAX_WAITING=0 \
  nohup .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$MOCK_PORT" \
  > "$OUT_DIR/mock.log" 2>&1 &
MOCK_PID=$!
cd "$ROOT"
for _ in $(seq 1 30); do curl -sf "http://127.0.0.1:${MOCK_PORT}/healthz" >/dev/null 2>&1 && break; sleep 0.5; done
if ! curl -sf "http://127.0.0.1:${MOCK_PORT}/healthz" >/dev/null 2>&1; then fail "Mock did not become healthy"; fi

GW_JAR="${ROOT}/${GW_DIR}/build/libs/${GW_JAR_GLOB}"
if [ ! -f "$GW_JAR" ]; then fail "Gateway jar not found: $GW_JAR"; fi

PROCESS_START_MS=$(now_ms)
cd "${ROOT}/${GW_DIR}"
if [ "$MODEL" = "m1" ]; then
  PT_WORKER_COUNT=50 PT_QUEUE_CAPACITY=500 \
  SERVLET_WRITE_POOL_SIZE=64 SERVLET_WRITE_QUEUE_CAPACITY=20000 SERVLET_PER_STREAM_BUFFER_CAPACITY=8 \
  SERVER_TOMCAT_MAX_CONNECTIONS=$OPEN_TOMCAT_MAX_CONNECTIONS \
  CHAT_TOTAL_TIMEOUT_MS=$CHAT_TOTAL_TIMEOUT_MS MOCK_LLM_BASE_URL="http://127.0.0.1:${MOCK_PORT}" SERVER_PORT=$GW_PORT \
    nohup "${JAVA_HOME}/bin/java" -jar "$GW_JAR" > "$OUT_DIR/gateway.log" 2>&1 &
elif [ "$MODEL" = "m2" ]; then
  SERVLET_WRITE_POOL_SIZE=64 SERVLET_WRITE_QUEUE_CAPACITY=20000 SERVLET_PER_STREAM_BUFFER_CAPACITY=8 \
  SERVER_TOMCAT_MAX_CONNECTIONS=$OPEN_TOMCAT_MAX_CONNECTIONS \
  CHAT_TOTAL_TIMEOUT_MS=$CHAT_TOTAL_TIMEOUT_MS MOCK_LLM_BASE_URL="http://127.0.0.1:${MOCK_PORT}" SERVER_PORT=$GW_PORT \
    nohup "${JAVA_HOME}/bin/java" -jar "$GW_JAR" > "$OUT_DIR/gateway.log" 2>&1 &
else
  WEBCLIENT_MAX_CONNECTIONS=$OPEN_M3_MAX_CONNECTIONS WEBCLIENT_PENDING_ACQUIRE_MAX_COUNT=$OPEN_M3_PENDING_ACQUIRE_MAX_COUNT \
  CHAT_TOTAL_TIMEOUT_MS=$CHAT_TOTAL_TIMEOUT_MS MOCK_LLM_BASE_URL="http://127.0.0.1:${MOCK_PORT}" SERVER_PORT=$GW_PORT \
    nohup "${JAVA_HOME}/bin/java" -jar "$GW_JAR" > "$OUT_DIR/gateway.log" 2>&1 &
fi
GW_PID=$!
cd "$ROOT"

for _ in $(seq 1 40); do curl -sf "http://127.0.0.1:${GW_PORT}/healthz" >/dev/null 2>&1 && break; sleep 0.5; done
if ! curl -sf "http://127.0.0.1:${GW_PORT}/healthz" >/dev/null 2>&1; then fail "Gateway did not become healthy"; fi

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

PROM_TSDB=$(mktemp -d)
nohup "$PROMETHEUS_BIN" --config.file="$PROM_CONFIG" --storage.tsdb.path="$PROM_TSDB" \
  --web.listen-address="127.0.0.1:${PROM_PORT}" --log.level=warn \
  > "$OUT_DIR/prometheus.log" 2>&1 &
PROM_PID=$!
for _ in $(seq 1 30); do curl -sf "http://127.0.0.1:${PROM_PORT}/-/ready" >/dev/null 2>&1 && break; sleep 0.5; done

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

curl -sS "http://127.0.0.1:${GW_PORT}/actuator/prometheus" > "$OUT_DIR/gateway-metrics-before-wave.txt"

# ---- VU pool sizing (FINAL, docs/decisions/phase4-open-loadgen-vu-sizing.md) ----
# Root cause of the original M1 R=10 dropped_iterations (Unit 7.3 audit): vus.max=450 never reached
# the old MAX_VUS=800 ceiling -- the drop was a VU-pool GROWTH-PACING failure (pre-allocated only
# 200, had to more than double live while M1 was independently degrading toward its own
# CHAT_TOTAL_TIMEOUT_MS), not a ceiling-sizing problem. Fix: pre-allocate up to the app's own
# worst-case timeout bound up front (so a sudden degradation never requires live growth at all),
# with a larger, headroomed MAX_VUS as a true emergency backstop. Both scale with this run's own
# RATE, not with SAFE_OPEN_MAX, so low-rate runs stay small. Empirically validated at RATE=256 (the
# top of the frozen range) against Direct-Mock: zero drops, actual peak VU usage (2146) far below
# the pre-allocated pool, k6 RSS ~3.05GB on a 16GB host -- not a host-safety risk (ADR section 3).
VU_TIMEOUT_S=$(( CHAT_TOTAL_TIMEOUT_MS / 1000 ))
PRE_ALLOCATED_VUS=$(( RATE * VU_TIMEOUT_S )); [ "$PRE_ALLOCATED_VUS" -gt 20000 ] && PRE_ALLOCATED_VUS=20000
MAX_VUS=$(( PRE_ALLOCATED_VUS * 5 / 4 )); [ "$MAX_VUS" -gt 25000 ] && MAX_VUS=25000

# ---- Single continuous k6 process: warmup immediately followed by measurement, no drain between ----
K6_WALL_START_MS=$(now_ms)
"$K6" run \
  -e RATE="$RATE" \
  -e WARMUP_SEC="$WARMUP_SEC" \
  -e MEASUREMENT_SEC="$MEASUREMENT_SEC" \
  -e GATEWAY_URL="http://127.0.0.1:${GW_PORT}/chat/stream" \
  -e FIRST_CHUNK_DELAY_MS=$FIRST_CHUNK_DELAY_MS \
  -e CHUNK_INTERVAL_MS=$CHUNK_INTERVAL_MS \
  -e CHUNK_COUNT=$CHUNK_COUNT \
  -e PRE_ALLOCATED_VUS="$PRE_ALLOCATED_VUS" \
  -e MAX_VUS="$MAX_VUS" \
  --summary-export "$OUT_DIR/k6-summary.json" \
  "$SCENARIO" > "$OUT_DIR/k6-stdout.log" 2>&1
K6_EXIT=$?
K6_WALL_END_MS=$(now_ms)
echo "$K6_EXIT" > "$OUT_DIR/k6-exit-code.txt"

# ---- Drain (after both phases complete) ----
DRAIN_START_MS=$(now_ms)
for _ in $(seq 1 60); do
  ACTIVE=$(curl -sS "http://127.0.0.1:${GW_PORT}/actuator/prometheus" 2>/dev/null | awk '/^gateway_active_requests /{print $2}')
  if [ "${ACTIVE:-1}" = "0" ] || [ "${ACTIVE:-1}" = "0.0" ]; then break; fi
  sleep 0.5
done
DRAIN_END_MS=$(now_ms)

curl -sS "http://127.0.0.1:${GW_PORT}/actuator/prometheus" > "$OUT_DIR/gateway-metrics-final.txt"
curl -sS "http://127.0.0.1:${MOCK_PORT}/metrics" > "$OUT_DIR/mock-metrics-final.txt"

Q_START=$(( (PROCESS_START_MS/1000) - 2 ))
Q_END=$(( (DRAIN_END_MS/1000) + 2 ))
for metric in gateway_active_requests jvm_threads_live_threads process_cpu_usage system_cpu_count \
              process_files_open_files process_files_max_files jvm_memory_used_bytes \
              executor_active executor_queue_depth executor_rejected_total \
              virtual_tasks_active virtual_tasks_started_total \
              tomcat_connections_current_connections tomcat_threads_busy_threads \
              reactor_netty_connection_provider_active_connections \
              reactor_netty_connection_provider_pending_connections \
              reactor_netty_connection_provider_max_connections; do
  curl -sS -G "http://127.0.0.1:${PROM_PORT}/api/v1/query_range" \
    --data-urlencode "query=${metric}{model=\"${PROM_JOB_MODEL}\"}" \
    --data-urlencode "start=${Q_START}" --data-urlencode "end=${Q_END}" --data-urlencode "step=1" \
    > "$OUT_DIR/prom-${metric}.json" 2>/dev/null
done

{
  echo "gateway_active_requests_final=$(awk '/^gateway_active_requests /{print $2}' "$OUT_DIR/gateway-metrics-final.txt")"
  echo "gateway_upstream_active_final=$(awk '/^gateway_upstream_active /{print $2}' "$OUT_DIR/gateway-metrics-final.txt")"
  if [ "$MODEL" = "m1" ]; then
    echo "executor_active_final=$(awk '/^executor_active /{print $2}' "$OUT_DIR/gateway-metrics-final.txt")"
    echo "executor_queue_depth_final=$(awk '/^executor_queue_depth /{print $2}' "$OUT_DIR/gateway-metrics-final.txt")"
  elif [ "$MODEL" = "m2" ]; then
    echo "virtual_tasks_active_final=$(awk '/^virtual_tasks_active /{print $2}' "$OUT_DIR/gateway-metrics-final.txt")"
  else
    echo "reactor_netty_pending_final=$(awk '/^reactor_netty_connection_provider_pending_connections/{print $2}' "$OUT_DIR/gateway-metrics-final.txt" | head -1)"
  fi
  echo "mock_active_requests_final=$(awk '/^mockllm_active_requests /{print $2}' "$OUT_DIR/mock-metrics-final.txt")"
  echo "mock_waiting_requests_final=$(awk '/^mockllm_waiting_requests /{print $2}' "$OUT_DIR/mock-metrics-final.txt")"
} > "$OUT_DIR/postflight.txt"

SERVER_TOMCAT_MAX_CONNECTIONS_JSON="null"
if [ "$MODEL" = "m1" ] || [ "$MODEL" = "m2" ]; then
  SERVER_TOMCAT_MAX_CONNECTIONS_JSON="$OPEN_TOMCAT_MAX_CONNECTIONS"
fi

cat > "$OUT_DIR/environment.json" <<EOF
{
  "screening_epoch": "${SCREENING_EPOCH:-open-final-client-lifecycle-v1}",
  "model": "$MODEL",
  "target_rate": $RATE,
  "run_label": "$RUN_LABEL",
  "warmup_sec": $WARMUP_SEC,
  "measurement_sec": $MEASUREMENT_SEC,
  "gateway_port": $GW_PORT,
  "mock_port": $MOCK_PORT,
  "prometheus_port": $PROM_PORT,
  "prometheus_scrape_interval": "1s",
  "chat_total_timeout_ms": $CHAT_TOTAL_TIMEOUT_MS,
  "workload": {"firstChunkDelayMs": $FIRST_CHUNK_DELAY_MS, "chunkIntervalMs": $CHUNK_INTERVAL_MS, "chunkCount": $CHUNK_COUNT, "chunkSizeBytes": 64},
  "gateway_jar": "$GW_JAR_GLOB",
  "gateway_pid": $GW_PID,
  "jdk_home": "$JAVA_HOME",
  "open_m3_max_connections": $OPEN_M3_MAX_CONNECTIONS,
  "open_m3_pending_acquire_max_count": $OPEN_M3_PENDING_ACQUIRE_MAX_COUNT,
  "server_tomcat_max_connections": $SERVER_TOMCAT_MAX_CONNECTIONS_JSON,
  "k6_pre_allocated_vus": $PRE_ALLOCATED_VUS,
  "k6_max_vus": $MAX_VUS
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

log "=== Done: $MODEL R=$RATE ($RUN_LABEL) -> $OUT_DIR ==="
