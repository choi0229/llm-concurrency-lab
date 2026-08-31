#!/usr/bin/env bash
# Phase 4 Open Unit 7.2 -- R=160 Transport/Front-door Isolation DIAGNOSTIC harness.
#
# NOT a Screening/Formal harness. Produces diagnostic-only artifacts under
# docs/test-results/phase4/unit7.2-r160-frontdoor-isolation/ -- never auto-promoted to canonical
# Screening state. Forked from scripts/run-phase4-open-benchmark.sh (frozen, untouched) with two
# additions: (1) a "direct-mock" target that removes the Gateway entirely (k6 -> Mock LLM directly,
# mirroring scripts/run-phase4-direct-mock-open-control.sh's URL pattern), and (2) client-host TCP
# socket telemetry sampling (scripts/sample-client-socket-telemetry.sh) running alongside k6. Uses
# load-test-k6/scenarios/06-phase4-open-diagnostic-r160.js (the new client-observability fork of 05)
# instead of 05 directly. Workload/timing (R=160, warmup=60s, measurement=120s, same SSE shape, same
# VU-sizing formula) is IDENTICAL to the canonical Open Screening harness for R=160 -- only
# observability changes.
#
# Usage: run-phase4-open-diagnostic-r160.sh <target:direct-mock|m2> <run_label> [rate]
# Unit 7.5: RATE is now an optional 3rd argument (default 160, unchanged) so this same harness can
# also run the low-rate smoke points Unit 7.5 section 7/8 calls for, reusing the identical
# client-lifecycle-fixed scenario (06) and endpoint-aware socket telemetry rather than duplicating
# the harness.
set -uo pipefail

TARGET="${1:?target required: direct-mock|m2}"
RUN_LABEL="${2:?run_label required}"
RATE="${3:-160}"
WARMUP_SEC=60
MEASUREMENT_SEC=120

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JAVA_HOME="${ROOT}/.native-runtime/jdk-21.0.11+10/Contents/Home"
K6="${ROOT}/.native-runtime/k6"
PROMETHEUS_BIN="${ROOT}/.native-runtime/prometheus-3.13.2.darwin-arm64/prometheus"
PROM_CONFIG="${ROOT}/monitoring/prometheus/prometheus-phase4.yml"
SCENARIO="${ROOT}/load-test-k6/scenarios/06-phase4-open-diagnostic-r160.js"
SOCKET_SAMPLER="${ROOT}/scripts/sample-client-socket-telemetry.sh"
MOCK_DIR="${ROOT}/mock-llm-fastapi"

MOCK_PORT=8000
PROM_PORT=9094
CHAT_TOTAL_TIMEOUT_MS=60000
FIRST_CHUNK_DELAY_MS=1000
CHUNK_INTERVAL_MS=200
CHUNK_COUNT=35
# Phase 4 Open Unit 7.3 amendment (docs/decisions/phase4-open-tomcat-connector-headroom.md) --
# applied here too so this script can be reused as the post-amendment M2 R=160 regression check
# (Unit 7.3 section 14), not just the pre-amendment Unit 7.2 diagnostic.
OPEN_TOMCAT_MAX_CONNECTIONS=3200

case "$TARGET" in
  direct-mock) TARGET_PORT=$MOCK_PORT; TARGET_URL="http://127.0.0.1:${MOCK_PORT}/mock/stream" ;;
  m2) GW_DIR="gateway-phase4-virtual-thread"; GW_JAR_GLOB="gateway-phase4-virtual-thread-0.1.0.jar"; GW_PORT=18102; PROM_JOB_MODEL="m2-virtual-thread"; TARGET_PORT=$GW_PORT; TARGET_URL="http://127.0.0.1:${GW_PORT}/chat/stream" ;;
  *) echo "FATAL: unknown target '$TARGET' (expected direct-mock|m2)" >&2; exit 1 ;;
esac

# Unit 7.5: route non-R=160 runs (the new low-rate smoke points) into the Unit 7.5 results
# directory instead of the Unit 7.2 R=160-specific one; R=160 runs keep going to unit7.2's
# directory for continuity with the existing regression/probe history there.
if [ "$RATE" = "160" ]; then
  OUT_ROOT="${ROOT}/docs/test-results/phase4/unit7.2-r160-frontdoor-isolation"
else
  OUT_ROOT="${ROOT}/docs/test-results/phase4/unit7.5-client-lifecycle-fix"
fi
OUT_DIR="${OUT_ROOT}/${TARGET}-r${RATE}-${RUN_LABEL}"
mkdir -p "$OUT_DIR"

log() { echo "[$(date '+%H:%M:%S')] $*"; }
port_free() { ! lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }
now_ms() { python3 -c 'import time; print(int(time.time()*1000))'; }

FAIL_REASON=""
fail() { FAIL_REASON="$1"; log "FATAL: $1"; }

log "=== Preflight: DIAGNOSTIC target=$TARGET R=$RATE ($RUN_LABEL) warmup=${WARMUP_SEC}s measurement=${MEASUREMENT_SEC}s ==="
PORTS_TO_CHECK=("$MOCK_PORT" "$PROM_PORT")
[ "$TARGET" = "m2" ] && PORTS_TO_CHECK+=("$GW_PORT")
for p in "${PORTS_TO_CHECK[@]}"; do
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

PROCESS_START_MS=$(now_ms)
GW_PID=""
if [ "$TARGET" = "m2" ]; then
  GW_JAR="${ROOT}/${GW_DIR}/build/libs/${GW_JAR_GLOB}"
  if [ ! -f "$GW_JAR" ]; then fail "Gateway jar not found: $GW_JAR"; fi
  cd "${ROOT}/${GW_DIR}"
  SERVLET_WRITE_POOL_SIZE=64 SERVLET_WRITE_QUEUE_CAPACITY=20000 SERVLET_PER_STREAM_BUFFER_CAPACITY=8 \
  SERVER_TOMCAT_MAX_CONNECTIONS=$OPEN_TOMCAT_MAX_CONNECTIONS \
  CHAT_TOTAL_TIMEOUT_MS=$CHAT_TOTAL_TIMEOUT_MS MOCK_LLM_BASE_URL="http://127.0.0.1:${MOCK_PORT}" SERVER_PORT=$GW_PORT \
    nohup "${JAVA_HOME}/bin/java" -jar "$GW_JAR" > "$OUT_DIR/gateway.log" 2>&1 &
  GW_PID=$!
  cd "$ROOT"
  for _ in $(seq 1 40); do curl -sf "http://127.0.0.1:${GW_PORT}/healthz" >/dev/null 2>&1 && break; sleep 0.5; done
  if ! curl -sf "http://127.0.0.1:${GW_PORT}/healthz" >/dev/null 2>&1; then fail "Gateway did not become healthy"; fi
  ACTUAL_CMD=$(ps -o command= -p "$GW_PID" 2>/dev/null)
  {
    echo "GATEWAY_PID=$GW_PID"
    echo "expected_jar=$GW_JAR_GLOB"
    echo "actual_command=$ACTUAL_CMD"
    if echo "$ACTUAL_CMD" | grep -q "$GW_JAR_GLOB"; then echo "PID_MATCH=true"; else echo "PID_MATCH=false"; fail "Gateway PID $GW_PID command does not contain expected jar name $GW_JAR_GLOB"; fi
  } > "$OUT_DIR/pid-verification.txt"
fi

PROM_TSDB=$(mktemp -d)
nohup "$PROMETHEUS_BIN" --config.file="$PROM_CONFIG" --storage.tsdb.path="$PROM_TSDB" \
  --web.listen-address="127.0.0.1:${PROM_PORT}" --log.level=warn \
  > "$OUT_DIR/prometheus.log" 2>&1 &
PROM_PID=$!
for _ in $(seq 1 30); do curl -sf "http://127.0.0.1:${PROM_PORT}/-/ready" >/dev/null 2>&1 && break; sleep 0.5; done

echo "epoch_s,rss_kib,pcpu" > "$OUT_DIR/rss-samples.csv"
SAMPLED_PID="${GW_PID:-$MOCK_PID}"
( while kill -0 "$SAMPLED_PID" 2>/dev/null; do
    rss=$(ps -o rss= -p "$SAMPLED_PID" 2>/dev/null | tr -d ' ')
    cpu=$(ps -o %cpu= -p "$SAMPLED_PID" 2>/dev/null | tr -d ' ')
    echo "$(date +%s),${rss:-0},${cpu:-0}" >> "$OUT_DIR/rss-samples.csv"
    sleep 1
  done ) &
SAMPLER_PID=$!

if [ -n "$FAIL_REASON" ]; then
  echo "$FAIL_REASON" > "$OUT_DIR/postflight.txt"
  kill "$SAMPLER_PID" "$PROM_PID" "$GW_PID" "$MOCK_PID" 2>/dev/null
  exit 1
fi

if [ "$TARGET" = "m2" ]; then
  curl -sS "http://127.0.0.1:${GW_PORT}/actuator/prometheus" > "$OUT_DIR/gateway-metrics-before-wave.txt"
fi

# Final VU formula (docs/decisions/phase4-open-loadgen-vu-sizing.md), same as the canonical harness.
VU_TIMEOUT_S=$(( CHAT_TOTAL_TIMEOUT_MS / 1000 ))
PRE_ALLOCATED_VUS=$(( RATE * VU_TIMEOUT_S )); [ "$PRE_ALLOCATED_VUS" -gt 20000 ] && PRE_ALLOCATED_VUS=20000
MAX_VUS=$(( PRE_ALLOCATED_VUS * 5 / 4 )); [ "$MAX_VUS" -gt 25000 ] && MAX_VUS=25000

# ---- Launch k6 in the background so the socket-telemetry sampler can run concurrently against
# its PID (unlike the canonical harness, which runs k6 in the foreground and blocks) ----
K6_WALL_START_MS=$(now_ms)
FD_SOFT_AT_K6_LAUNCH=$(ulimit -n)
"$K6" run \
  -e RATE="$RATE" \
  -e WARMUP_SEC="$WARMUP_SEC" \
  -e MEASUREMENT_SEC="$MEASUREMENT_SEC" \
  -e GATEWAY_URL="$TARGET_URL" \
  -e FIRST_CHUNK_DELAY_MS=$FIRST_CHUNK_DELAY_MS \
  -e CHUNK_INTERVAL_MS=$CHUNK_INTERVAL_MS \
  -e CHUNK_COUNT=$CHUNK_COUNT \
  -e PRE_ALLOCATED_VUS="$PRE_ALLOCATED_VUS" \
  -e MAX_VUS="$MAX_VUS" \
  --summary-export "$OUT_DIR/k6-summary.json" \
  "$SCENARIO" > "$OUT_DIR/k6-stdout.log" 2>&1 &
K6_PID=$!

"$SOCKET_SAMPLER" "$K6_PID" "$TARGET_PORT" "$OUT_DIR/client-socket-telemetry.csv" "$OUT_DIR/client-socket-telemetry-meta.json" &
SOCKET_SAMPLER_PID=$!

wait "$K6_PID"
K6_EXIT=$?
K6_WALL_END_MS=$(now_ms)
echo "$K6_EXIT" > "$OUT_DIR/k6-exit-code.txt"
echo "$FD_SOFT_AT_K6_LAUNCH" > "$OUT_DIR/fd-soft-limit-at-k6-launch.txt"
wait "$SOCKET_SAMPLER_PID" 2>/dev/null

# ---- Drain ----
DRAIN_START_MS=$(now_ms)
if [ "$TARGET" = "m2" ]; then
  for _ in $(seq 1 60); do
    ACTIVE=$(curl -sS "http://127.0.0.1:${GW_PORT}/actuator/prometheus" 2>/dev/null | awk '/^gateway_active_requests /{print $2}')
    if [ "${ACTIVE:-1}" = "0" ] || [ "${ACTIVE:-1}" = "0.0" ]; then break; fi
    sleep 0.5
  done
else
  for _ in $(seq 1 60); do
    ACTIVE=$(curl -sS "http://127.0.0.1:${MOCK_PORT}/metrics" 2>/dev/null | awk '/^mockllm_active_requests /{print $2}')
    if [ "${ACTIVE:-1}" = "0" ] || [ "${ACTIVE:-1}" = "0.0" ]; then break; fi
    sleep 0.5
  done
fi
DRAIN_END_MS=$(now_ms)

if [ "$TARGET" = "m2" ]; then
  curl -sS "http://127.0.0.1:${GW_PORT}/actuator/prometheus" > "$OUT_DIR/gateway-metrics-final.txt"
fi
curl -sS "http://127.0.0.1:${MOCK_PORT}/metrics" > "$OUT_DIR/mock-metrics-final.txt"

if [ "$TARGET" = "m2" ]; then
  Q_START=$(( (PROCESS_START_MS/1000) - 2 ))
  Q_END=$(( (DRAIN_END_MS/1000) + 2 ))
  for metric in gateway_active_requests jvm_threads_live_threads process_cpu_usage system_cpu_count \
                process_files_open_files process_files_max_files jvm_memory_used_bytes \
                virtual_tasks_active virtual_tasks_started_total \
                tomcat_threads_busy_threads tomcat_threads_current_threads \
                tomcat_connections_current_connections; do
    curl -sS -G "http://127.0.0.1:${PROM_PORT}/api/v1/query_range" \
      --data-urlencode "query=${metric}{model=\"${PROM_JOB_MODEL}\"}" \
      --data-urlencode "start=${Q_START}" --data-urlencode "end=${Q_END}" --data-urlencode "step=1" \
      > "$OUT_DIR/prom-${metric}.json" 2>/dev/null
  done
fi

cat > "$OUT_DIR/environment.json" <<EOF
{
  "diagnostic_unit": "7.2-r160-frontdoor-isolation",
  "canonical": false,
  "target": "$TARGET",
  "target_rate": $RATE,
  "run_label": "$RUN_LABEL",
  "warmup_sec": $WARMUP_SEC,
  "measurement_sec": $MEASUREMENT_SEC,
  "target_url": "$TARGET_URL",
  "mock_port": $MOCK_PORT,
  "prometheus_port": $PROM_PORT,
  "chat_total_timeout_ms": $CHAT_TOTAL_TIMEOUT_MS,
  "workload": {"firstChunkDelayMs": $FIRST_CHUNK_DELAY_MS, "chunkIntervalMs": $CHUNK_INTERVAL_MS, "chunkCount": $CHUNK_COUNT, "chunkSizeBytes": 64},
  "k6_pre_allocated_vus": $PRE_ALLOCATED_VUS,
  "k6_max_vus": $MAX_VUS,
  "fd_soft_limit_at_k6_launch": "$FD_SOFT_AT_K6_LAUNCH",
  "server_tomcat_max_connections": $( [ "$TARGET" = "m2" ] && echo "$OPEN_TOMCAT_MAX_CONNECTIONS" || echo "null" )
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
[ -n "$GW_PID" ] && kill "$GW_PID" 2>/dev/null
kill "$PROM_PID" 2>/dev/null
kill "$MOCK_PID" 2>/dev/null
for _ in $(seq 1 20); do
  ALL_FREE=1
  for p in "${PORTS_TO_CHECK[@]}"; do port_free "$p" || ALL_FREE=0; done
  [ "$ALL_FREE" = "1" ] && break
  sleep 0.5
done
rm -rf "$PROM_TSDB"

if [ -n "$FAIL_REASON" ]; then
  echo "FAIL: $FAIL_REASON" >> "$OUT_DIR/postflight.txt"
  exit 1
fi
echo "OK" >> "$OUT_DIR/postflight.txt"

log "=== Done: DIAGNOSTIC target=$TARGET R=$RATE ($RUN_LABEL) -> $OUT_DIR ==="
