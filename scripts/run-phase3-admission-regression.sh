#!/usr/bin/env bash
# Unit 5 common functional cross-validation harness (docs/test-plan/phase3-design.md, Unit 5 §4).
#
# NOT a benchmark harness: no warm-up measurement, no repeated measurement, no RPS/concurrency
# sweep, no formal aggregation. One fresh Gateway JVM per (implementation x scenario) pair, one
# request (or two, for F3) each, before/after Prometheus counter snapshots, gauge postflight
# snapshot. Never runs P3-A/B/C concurrently against each other (Unit 5 §4 -- no resource
# contention between configs).
#
# Usage: scripts/run-phase3-functional-cross-validation.sh [A|B|C|all]
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JAVA8="$REPO_ROOT/.native-runtime/zulu8.96.0.205-ca-jdk8.0.504-macosx_aarch64/Contents/Home"
DIFF_HELPER="$REPO_ROOT/scripts/phase3-prometheus-diff.py"
SCRATCH="${PHASE3_SCRATCH:-/tmp/phase3-cross-validation}"
EVIDENCE_ROOT="$REPO_ROOT/docs/test-results/phase3/unit6-admission-regression"
MOCK_DIR="$REPO_ROOT/mock-llm-fastapi"
MOCK_PORT=8000
MOCK_URL="http://127.0.0.1:${MOCK_PORT}"
REFUSED_PORT=18999   # Unit 5 §12 -- verified free immediately before each use, never bound

mkdir -p "$SCRATCH" "$EVIDENCE_ROOT"

# Portable (bash 3.2 -- macOS's default, no associative arrays) lookups instead of `declare -A`.
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

log() { echo "[harness] $*"; }

port_is_free() {
  ! (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
}

start_mock() {
  pkill -f "uvicorn app.main:app" 2>/dev/null || true
  sleep 1
  ( cd "$MOCK_DIR" && nohup .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$MOCK_PORT" \
      > "$SCRATCH/mock-llm.log" 2>&1 & disown )
  for _ in $(seq 1 20); do
    curl -s -o /dev/null "http://127.0.0.1:${MOCK_PORT}/healthz" && return 0
    sleep 0.5
  done
  echo "Mock LLM failed to start" >&2; exit 1
}

stop_mock() {
  pkill -f "uvicorn app.main:app" 2>/dev/null || true
  sleep 1
}

find_jar() {
  find "$1/build/libs" -maxdepth 1 -name "*.jar" ! -name "*-plain.jar" 2>/dev/null | head -1
}

# start_gateway CFG PORT ENV_VAR=value...
start_gateway() {
  local cfg="$1" port="$2"; shift 2
  local dir; dir="$(module_dir "$cfg")"
  local jar; jar="$(find_jar "$dir")"
  if [ -z "$jar" ]; then
    log "building $cfg jar..."
    ( cd "$dir" && JAVA_HOME="$JAVA8" ./gradlew --no-daemon bootJar >> "$SCRATCH/build-$cfg.log" 2>&1 )
    jar="$(find_jar "$dir")"
  fi
  pkill -f "$(basename "$jar")" 2>/dev/null || true
  sleep 1
  (
    cd "$dir"
    export SERVER_PORT="$port"
    export MOCK_LLM_BASE_URL="$MOCK_URL"
    for kv in "$@"; do export "${kv?}"; done
    nohup "$JAVA8/bin/java" -jar "$jar" > "$SCRATCH/gateway-$cfg.log" 2>&1 &
    disown
  )
  for _ in $(seq 1 40); do
    curl -s -o /dev/null "http://127.0.0.1:${port}/healthz" && return 0
    sleep 0.5
  done
  log "Gateway $cfg failed to start -- see $SCRATCH/gateway-$cfg.log"
  return 1
}

stop_gateway() {
  local cfg="$1"; local dir; dir="$(module_dir "$1")"
  local jar; jar="$(find_jar "$dir")"
  [ -n "$jar" ] && pkill -f "$(basename "$jar")" 2>/dev/null
  sleep 1
}

snapshot() {
  curl -s "http://127.0.0.1:$1/actuator/prometheus" > "$2"
}

# run_scenario CFG SCENARIO_NAME PORT -- body follows as a function reference
run_scenario() {
  local cfg="$1" name="$2" port="$3"
  local dir="$EVIDENCE_ROOT/$cfg/$name"
  mkdir -p "$dir"
  snapshot "$port" "$dir/prometheus-before.txt"
  echo "$dir"
}

finish_scenario() {
  local dir="$1" port="$2"
  sleep 1.5 # let async cleanup (permit release, doFinally, etc.) settle before the after-snapshot
  snapshot "$port" "$dir/prometheus-after.txt"
  {
    echo "=== counter deltas ==="
    python3 "$DIFF_HELPER" counters "$dir/prometheus-before.txt" "$dir/prometheus-after.txt"
    echo "=== gauge postflight ==="
    python3 "$DIFF_HELPER" gauges "$dir/prometheus-after.txt"
  } > "$dir/accounting.txt"
  cat "$dir/accounting.txt"
}

# ---- Scenario bodies -------------------------------------------------------

scenario_F1() {
  local cfg="$1" port="$2"
  local dir; dir=$(run_scenario "$cfg" F1-normal-sse "$port")
  curl -s -N -X POST "http://127.0.0.1:${port}/chat/stream" -H "Content-Type: application/json" \
    -d '{"firstChunkDelayMs":300,"chunkIntervalMs":150,"chunkCount":5,"chunkSizeBytes":16}' \
    -D "$dir/response-headers.txt" -o "$dir/response-body.txt" \
    -w "HTTP_STATUS:%{http_code} TIME_TOTAL:%{time_total}s TIME_STARTTRANSFER:%{time_starttransfer}s\n" \
    > "$dir/curl-summary.txt"
  cat "$dir/curl-summary.txt"
  finish_scenario "$dir" "$port"
}

scenario_F2() {
  local cfg="$1" port="$2"
  local dir; dir=$(run_scenario "$cfg" F2-absolute-deadline "$port")
  curl -s -N -X POST "http://127.0.0.1:${port}/chat/stream" -H "Content-Type: application/json" \
    -d '{"firstChunkDelayMs":100,"chunkIntervalMs":1000,"chunkCount":10,"chunkSizeBytes":16,"stallAfterChunk":2,"stallMs":30000}' \
    -o "$dir/response-body.txt" \
    -w "HTTP_STATUS:%{http_code} TIME_TOTAL:%{time_total}s\n" \
    > "$dir/curl-summary.txt"
  cat "$dir/curl-summary.txt"
  finish_scenario "$dir" "$port"
}

scenario_F3() {
  local cfg="$1" port="$2"
  local dir; dir=$(run_scenario "$cfg" F3-admission "$port")
  curl -s -N -X POST "http://127.0.0.1:${port}/chat/stream" -H "Content-Type: application/json" \
    -d '{"firstChunkDelayMs":100,"chunkIntervalMs":800,"chunkCount":6,"chunkSizeBytes":16}' \
    -o "$dir/request1-body.txt" -w "req1: HTTP_STATUS:%{http_code} TIME_TOTAL:%{time_total}s\n" \
    > "$dir/curl-summary.txt" &
  local first_pid=$!
  sleep 0.5
  curl -s -N -X POST "http://127.0.0.1:${port}/chat/stream" -H "Content-Type: application/json" \
    -d '{"firstChunkDelayMs":100,"chunkIntervalMs":100,"chunkCount":2,"chunkSizeBytes":16}' \
    -D "$dir/request2-headers.txt" -o "$dir/request2-body.txt" \
    -w "req2: HTTP_STATUS:%{http_code} TIME_TOTAL:%{time_total}s\n" \
    >> "$dir/curl-summary.txt"
  wait "$first_pid"
  cat "$dir/curl-summary.txt"
  finish_scenario "$dir" "$port"
}

scenario_F4() {
  local cfg="$1" port="$2"
  local dir; dir=$(run_scenario "$cfg" F4-client-disconnect "$port")
  curl -s -N -X POST "http://127.0.0.1:${port}/chat/stream" -H "Content-Type: application/json" \
    -d '{"firstChunkDelayMs":50,"chunkIntervalMs":50,"chunkCount":2,"chunkSizeBytes":8}' \
    -o /dev/null -w "warmup: %{time_total}s\n" > "$dir/curl-summary.txt"
  sleep 0.5
  curl -s -N --max-time 0.6 -X POST "http://127.0.0.1:${port}/chat/stream" -H "Content-Type: application/json" \
    -d '{"firstChunkDelayMs":100,"chunkIntervalMs":500,"chunkCount":20,"chunkSizeBytes":16}' \
    -o "$dir/response-body.txt" \
    -w "HTTP_STATUS:%{http_code} TIME_TOTAL:%{time_total}s\n" \
    >> "$dir/curl-summary.txt" 2>&1
  cat "$dir/curl-summary.txt"
  finish_scenario "$dir" "$port"
}

scenario_F5() {
  local cfg="$1" port="$2"
  local dir; dir=$(run_scenario "$cfg" F5-upstream-failure "$port")
  curl -s -N -X POST "http://127.0.0.1:${port}/chat/stream" -H "Content-Type: application/json" \
    -d '{"firstChunkDelayMs":100,"chunkIntervalMs":100,"chunkCount":3,"chunkSizeBytes":8}' \
    -o "$dir/response-body.txt" \
    -w "HTTP_STATUS:%{http_code} TIME_TOTAL:%{time_total}s\n" \
    > "$dir/curl-summary.txt"
  cat "$dir/curl-summary.txt"
  finish_scenario "$dir" "$port"
}

# ---- Per-config run ---------------------------------------------------------

run_config() {
  local cfg="$1"; local port; port="$(gateway_port "$cfg")"

  log "=== $cfg / F1 normal SSE (default config) ==="
  start_gateway "$cfg" "$port" "CHAT_TOTAL_TIMEOUT_MS=60000" || return 1
  scenario_F1 "$cfg" "$port"
  stop_gateway "$cfg"

  log "=== $cfg / F2 absolute deadline (CHAT_TOTAL_TIMEOUT_MS=2500) ==="
  start_gateway "$cfg" "$port" "CHAT_TOTAL_TIMEOUT_MS=2500" || return 1
  curl -s -N -X POST "http://127.0.0.1:${port}/chat/stream" -H "Content-Type: application/json" \
    -d '{"firstChunkDelayMs":50,"chunkIntervalMs":50,"chunkCount":2,"chunkSizeBytes":8}' -o /dev/null
  sleep 0.5
  scenario_F2 "$cfg" "$port"
  stop_gateway "$cfg"

  log "=== $cfg / F3 admission (CHAT_ADMISSION_LIMIT=1) ==="
  start_gateway "$cfg" "$port" "CHAT_TOTAL_TIMEOUT_MS=60000" "CHAT_ADMISSION_LIMIT=1" "WEBCLIENT_MAX_CONNECTIONS=1" "CHAT_BLOCKING_POOL_SIZE=1" || return 1
  scenario_F3 "$cfg" "$port"
  stop_gateway "$cfg"

  log "=== $cfg / F4 client disconnect (default config) ==="
  start_gateway "$cfg" "$port" "CHAT_TOTAL_TIMEOUT_MS=60000" || return 1
  scenario_F4 "$cfg" "$port"
  stop_gateway "$cfg"

  log "=== $cfg / F5 upstream failure (connection refused on port $REFUSED_PORT) ==="
  if ! port_is_free "$REFUSED_PORT"; then
    log "port $REFUSED_PORT unexpectedly in use -- skipping F5 for $cfg"
  else
    start_gateway "$cfg" "$port" "CHAT_TOTAL_TIMEOUT_MS=10000" "MOCK_LLM_BASE_URL=http://127.0.0.1:${REFUSED_PORT}" || return 1
    scenario_F5 "$cfg" "$port"
    stop_gateway "$cfg"
  fi
}

# ---- Entry point ------------------------------------------------------------

TARGET="${1:-all}"
start_mock
trap stop_mock EXIT

if [ "$TARGET" = "all" ]; then
  for cfg in A B C; do
    start_mock # fresh Mock process per config (Unit 5 §4)
    run_config "$cfg"
  done
else
  run_config "$TARGET"
fi

log "done. Evidence under $EVIDENCE_ROOT"
