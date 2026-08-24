#!/usr/bin/env bash
# Phase 3 Unit 6 Screening (docs/decisions/phase3-admission-semantics-unification.md must be PASS
# before this runs). Short pilot runs (~25s) per (implementation x rate) pair, open-model
# constant-arrival-rate, Normal SSE Mock workload only (Slow Client workload NOT used here per
# docs/test-plan/phase3-design.md §10-2). NOT Formal: no repeats, no long duration, minimal raw
# artifact retention (Prometheus/RSS samples only, no full JFR/heap dump).
#
# Usage: scripts/run-phase3-native-screening.sh <A|B|C> <RATE> [DURATION] [LABEL]
set -uo pipefail

CFG="$1"; RATE="$2"; DURATION="${3:-25s}"; LABEL="${4:-r${RATE}}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JAVA8="$REPO_ROOT/.native-runtime/zulu8.96.0.205-ca-jdk8.0.504-macosx_aarch64/Contents/Home"
K6="$REPO_ROOT/.native-runtime/k6"
K6_SCRIPT="$REPO_ROOT/scripts/phase3-screening-k6.js"
MOCK_DIR="$REPO_ROOT/mock-llm-fastapi"
MOCK_PORT=8000
MOCK_URL="http://127.0.0.1:${MOCK_PORT}"
OUT_DIR="$REPO_ROOT/docs/test-results/phase3/unit6-screening/${CFG}/${LABEL}"
mkdir -p "$OUT_DIR"

module_dir() {
  case "$1" in
    A) echo "$REPO_ROOT/gateway-mvc-blocking-spring5" ;;
    B) echo "$REPO_ROOT/gateway-mvc-webclient" ;;
    C) echo "$REPO_ROOT/gateway-webflux" ;;
  esac
}
gateway_port() {
  case "$1" in
    A) echo 8081 ;;
    B) echo 8082 ;;
    C) echo 8083 ;;
  esac
}

DIR="$(module_dir "$CFG")"
PORT="$(gateway_port "$CFG")"
log() { echo "[screening][$CFG][$LABEL] $*"; }

find_jar() { find "$1/build/libs" -maxdepth 1 -name "*.jar" ! -name "*-plain.jar" 2>/dev/null | head -1; }

GATEWAY_PID=""; MOCK_PID=""; SAMPLER_PID=""; RSS_PID=""
cleanup() {
  for pidvar in SAMPLER_PID RSS_PID GATEWAY_PID MOCK_PID; do
    pid="${!pidvar}"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null
      for i in $(seq 1 10); do kill -0 "$pid" 2>/dev/null || break; sleep 0.3; done
      kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
    fi
  done
}
trap cleanup EXIT

# ---- fresh Mock LLM ----
pkill -f "uvicorn app.main:app" 2>/dev/null || true
sleep 1
( cd "$MOCK_DIR" && nohup .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$MOCK_PORT" \
    > "$OUT_DIR/mock-llm.log" 2>&1 & echo $! > "$OUT_DIR/mock.pid" )
MOCK_PID=$(cat "$OUT_DIR/mock.pid")
for _ in $(seq 1 20); do curl -s -o /dev/null "${MOCK_URL}/healthz" && break; sleep 0.5; done

# ---- fresh Gateway JVM (frozen config axes, docs/test-plan/phase3-design.md §9-2) ----
JAR="$(find_jar "$DIR")"
if [ -z "$JAR" ]; then
  log "building jar..."
  ( cd "$DIR" && JAVA_HOME="$JAVA8" ./gradlew --no-daemon bootJar >> "$OUT_DIR/build.log" 2>&1 )
  JAR="$(find_jar "$DIR")"
fi
pkill -f "$(basename "$JAR")" 2>/dev/null || true
sleep 1

ENV_ARGS=(SERVER_PORT="$PORT" MOCK_LLM_BASE_URL="$MOCK_URL" CHAT_ADMISSION_LIMIT=50 CHAT_TOTAL_TIMEOUT_MS=60000)
case "$CFG" in
  A) ENV_ARGS+=(CHAT_BLOCKING_POOL_SIZE=50) ;;
  B) ENV_ARGS+=(WEBCLIENT_MAX_CONNECTIONS=50) ;;
  C) ENV_ARGS+=(WEBCLIENT_MAX_CONNECTIONS=50) ;;
esac

( cd "$DIR" && env "${ENV_ARGS[@]}" nohup "$JAVA8/bin/java" -jar "$JAR" > "$OUT_DIR/gateway.log" 2>&1 & )
for _ in $(seq 1 40); do curl -s -o /dev/null "http://127.0.0.1:${PORT}/healthz" && break; sleep 0.5; done
curl -s -o /dev/null "http://127.0.0.1:${PORT}/healthz" || { log "FATAL: gateway not healthy"; exit 1; }
# $! inside the backgrounded subshell above is unreliable once env/nohup are involved (the PID
# recorded there was observed, by direct ps comparison, to NOT match the actual java process --
# an off-by-one/wrong-process bug that silently broke RSS sampling in the first Unit 6 screening
# pass; jvm_threads_live_threads/process_cpu_usage were unaffected since those come from the live
# /actuator/prometheus HTTP endpoint, not the PID). pgrep against the jar's basename, right after
# the healthz check above confirms the real process is up, is the reliable way to get it.
GATEWAY_PID=$(pgrep -f "$(basename "$JAR")" | head -1)
[ -n "$GATEWAY_PID" ] || { log "FATAL: could not resolve gateway PID via pgrep"; exit 1; }
echo "$GATEWAY_PID" > "$OUT_DIR/gateway.pid"

# ---- Warm-up (identical across configs, not part of measurement): 5 quick sequential
# requests, small workload distinct from the measured Normal body, to absorb JIT/connection-pool
# cold start (Unit 3/4 findings, docs/test-plan/phase3-design.md §9-3). ----
for i in 1 2 3 4 5; do
  curl -s -N -X POST "http://127.0.0.1:${PORT}/chat/stream" -H "Content-Type: application/json" \
    -d '{"firstChunkDelayMs":50,"chunkIntervalMs":50,"chunkCount":3,"chunkSizeBytes":16}' -o /dev/null
done
sleep 1

# ---- samplers ----
PROM_CSV="$OUT_DIR/prometheus-samples.csv"
echo "ts,jvm_threads_live,process_cpu_usage,admission_active,active_streams,upstream_active,servlet_write_active,servlet_write_queue,servlet_write_buffered,blocking_executor_active,conn_pending" > "$PROM_CSV"
( while kill -0 "$GATEWAY_PID" 2>/dev/null; do
    ts=$(date +%s.%N)
    m=$(curl -s -m 1 "http://127.0.0.1:${PORT}/actuator/prometheus" 2>/dev/null)
    g() { echo "$m" | awk -v k="$1" '$0 ~ "^"k" " {print $NF; exit} $0 ~ "^"k"\\{" {print $NF; exit}'; }
    echo "$ts,$(g jvm_threads_live_threads),$(g process_cpu_usage),$(g gateway_admission_active),$(g gateway_active_streams),$(g gateway_upstream_active),$(g servlet_write_executor_active),$(g servlet_write_executor_queue_depth),$(g servlet_write_stream_buffered_frames),$(g outbound_blocking_executor_active),$(g reactor_netty_connection_provider_pending_connections)" >> "$PROM_CSV"
    sleep 1
  done ) &
SAMPLER_PID=$!

RSS_CSV="$OUT_DIR/rss-samples.csv"
echo "ts,rss_kib" > "$RSS_CSV"
( while kill -0 "$GATEWAY_PID" 2>/dev/null; do
    ts=$(date +%s)
    rss=$(ps -o rss= -p "$GATEWAY_PID" 2>/dev/null | tr -d ' ')
    [ -n "$rss" ] && echo "$ts,$rss" >> "$RSS_CSV"
    sleep 1
  done ) &
RSS_PID=$!

log "pilot start rate=$RATE duration=$DURATION"
"$K6" run --summary-export "$OUT_DIR/k6-summary.json" \
  -e GATEWAY_URL="http://127.0.0.1:${PORT}/chat/stream" \
  -e RATE="$RATE" -e DURATION="$DURATION" \
  -e FIRST_CHUNK_DELAY_MS=1000 -e CHUNK_INTERVAL_MS=200 -e CHUNK_COUNT=35 -e CHUNK_SIZE_BYTES=64 \
  -e PRE_ALLOCATED_VUS=$((RATE * 20 + 50)) -e MAX_VUS=$((RATE * 60 + 150)) \
  "$K6_SCRIPT" > "$OUT_DIR/k6-output.txt" 2>&1
K6_EXIT=$?
log "k6 exit=$K6_EXIT"

sleep 3
kill "$SAMPLER_PID" "$RSS_PID" 2>/dev/null
curl -s "http://127.0.0.1:${PORT}/actuator/prometheus" > "$OUT_DIR/prometheus-postflight.txt"

tail -30 "$OUT_DIR/k6-output.txt"
echo "--- postflight gauges ---"
grep -E "^gateway_admission_active|^gateway_active_streams|^gateway_upstream_active|^servlet_write_executor_active|^servlet_write_executor_queue_depth|^servlet_write_stream_buffered_frames|^outbound_blocking_executor_active|^reactor_netty_connection_provider_pending_connections" "$OUT_DIR/prometheus-postflight.txt"
log "done -> $OUT_DIR"
