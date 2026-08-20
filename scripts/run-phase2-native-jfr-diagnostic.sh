#!/usr/bin/env bash
# Phase 2 Unit 8 B — JFR Virtual Thread Pinning Diagnostic (native macOS ARM64).
#
# Separate from the Formal harness (scripts/run-phase2-native-formal-benchmark.sh,
# frozen, never modified by this file) — this is diagnostic-only, JFR ON,
# NOT part of the Primary Formal dataset. Reuses the same fresh-process
# lifecycle/preflight/postflight/cleanup pattern as the Formal harness, but:
#   - launches Gateway with -XX:StartFlightRecording using the shared
#     phase2-vt-pinning.jfc (jdk.VirtualThreadPinned threshold=0ms)
#   - drives load with the closed-model scenario (01-concurrent-connections.js)
#     instead of the continuous warm-up->measurement Formal scenario
#   - does NOT run counter-plateau-check.json (Formal-only rigor, not needed
#     for a short diagnostic run) or collect_phase2_formal_result.py
#
# Usage: ./scripts/run-phase2-native-jfr-diagnostic.sh <label> <vus> \
#   <first_chunk_delay_ms> <chunk_interval_ms> <chunk_count> [read_timeout_ms]
set -uo pipefail

LABEL="$1"; VUS="$2"; FIRST_CHUNK_DELAY_MS="$3"; CHUNK_INTERVAL_MS="$4"; CHUNK_COUNT="$5"
CHAT_READ_TIMEOUT_MS="${6:-30000}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${ROOT}/docs/test-results/phase2/secondary/jfr-pinning-diagnostic/${LABEL}"
mkdir -p "${OUT_DIR}"
cd "${ROOT}"

JFC="${ROOT}/docs/test-results/phase2/secondary/jfr-pinning-diagnostic/phase2-vt-pinning.jfc"
NATIVE_DIR="${ROOT}/.native-runtime"
JDK_HOME="${NATIVE_DIR}/jdk-21.0.11+10/Contents/Home"
JAVA_BIN="${JDK_HOME}/bin/java"
JFR_BIN="${JDK_HOME}/bin/jfr"
K6_BIN="${NATIVE_DIR}/k6"
PROM_BIN="${NATIVE_DIR}/prometheus-3.13.2.darwin-arm64/prometheus"
MOCK_VENV="${ROOT}/mock-llm-fastapi/.venv"
PROM_PORT=9092
GATEWAY_PORT=8080
MOCK_PORT=8000

echo "=== [${LABEL}] $(date -u +%Y-%m-%dT%H:%M:%SZ) starting JFR diagnostic (VUS=${VUS}) ==="

for bin in "${JAVA_BIN}" "${JFR_BIN}" "${K6_BIN}" "${PROM_BIN}" "${MOCK_VENV}/bin/uvicorn" "${JFC}"; do
  if [ ! -e "${bin}" ]; then
    echo "[${LABEL}] FATAL: missing dependency: ${bin}" >&2
    exit 1
  fi
done

GATEWAY_PID=""; MOCK_PID=""; PROM_PID=""; CAFFEINATE_PID=""; PROM_TSDB_DIR=""
cleanup() {
  echo "[${LABEL}] cleanup: stopping native processes..."
  for pidvar in GATEWAY_PID MOCK_PID PROM_PID CAFFEINATE_PID; do
    pid="${!pidvar}"
    if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null
      for i in $(seq 1 20); do
        kill -0 "${pid}" 2>/dev/null || break
        sleep 0.5
      done
      kill -0 "${pid}" 2>/dev/null && kill -9 "${pid}" 2>/dev/null
    fi
  done
  [ -n "${PROM_TSDB_DIR}" ] && rm -rf "${PROM_TSDB_DIR}"
}
trap cleanup EXIT

# ---- Preflight (same checks as Formal harness) ----
PREFLIGHT_FAIL=""
if pgrep -fq "Docker Desktop|com.docker.backend"; then
  PREFLIGHT_FAIL="${PREFLIGHT_FAIL} docker_desktop_still_running"
fi
for p in ${GATEWAY_PORT} ${MOCK_PORT} ${PROM_PORT}; do
  if lsof -i ":${p}" -sTCP:LISTEN >/dev/null 2>&1; then
    PREFLIGHT_FAIL="${PREFLIGHT_FAIL} port_${p}_in_use"
  fi
done
if [ -n "${PREFLIGHT_FAIL}" ]; then
  echo "[${LABEL}] FATAL: preflight failed:${PREFLIGHT_FAIL}" >&2
  exit 1
fi

caffeinate -dims -w $$ &
CAFFEINATE_PID=$!

# ---- Fresh Prometheus ----
PROM_TSDB_DIR=$(mktemp -d "${TMPDIR:-/tmp}/phase2-native-jfr-prom-XXXXXX")
"${PROM_BIN}" \
  --config.file="${ROOT}/monitoring/prometheus/prometheus-native.yml" \
  --storage.tsdb.path="${PROM_TSDB_DIR}" \
  --web.listen-address=":${PROM_PORT}" \
  > "${OUT_DIR}/prometheus.log" 2>&1 &
PROM_PID=$!
for i in $(seq 1 30); do
  curl -s -m 2 "http://localhost:${PROM_PORT}/-/ready" >/dev/null 2>&1 && break
  sleep 1
done

# ---- Fresh Mock LLM (unlimited — this is Gateway-side pinning diagnostic,
# not a downstream capacity test) ----
MOCK_LLM_LOG_LEVEL=INFO MOCK_LLM_MAX_CONCURRENT_PROCESSING=0 MOCK_LLM_MAX_WAITING=0 \
  "${MOCK_VENV}/bin/uvicorn" app.main:app --host 0.0.0.0 --port "${MOCK_PORT}" \
  --app-dir "${ROOT}/mock-llm-fastapi" \
  > "${OUT_DIR}/mock-llm.log" 2>&1 &
MOCK_PID=$!
for i in $(seq 1 30); do
  curl -s -m 2 "http://localhost:${MOCK_PORT}/healthz" >/dev/null 2>&1 && break
  sleep 1
done
curl -s -m 2 "http://localhost:${MOCK_PORT}/healthz" >/dev/null 2>&1 || { echo "[${LABEL}] FATAL: Mock LLM not healthy" >&2; exit 1; }

# ---- Fresh Gateway JVM, JFR ON with the shared diagnostic JFC ----
# THREAD_MODE=VIRTUAL_LIMITED for both B-1/B-2 (per Unit 8 plan) — permits
# default (50) comfortably covers VUS<=50, this is a pinning diagnostic, not
# an admission-limits test.
env THREAD_MODE=VIRTUAL_LIMITED MOCK_LLM_BASE_URL="http://localhost:${MOCK_PORT}" \
  CHAT_CONNECT_TIMEOUT_MS=3000 CHAT_READ_TIMEOUT_MS="${CHAT_READ_TIMEOUT_MS}" CHAT_TOTAL_TIMEOUT_MS=60000 \
  "${JAVA_BIN}" -XX:StartFlightRecording=filename="${OUT_DIR}/recording.jfr",settings="${JFC}" \
  -jar "${ROOT}/gateway-mvc-java21/build/libs/gateway-mvc-java21-0.1.0.jar" \
  > "${OUT_DIR}/gateway.log" 2>&1 &
GATEWAY_PID=$!
for i in $(seq 1 30); do
  curl -s -m 2 "http://localhost:${GATEWAY_PORT}/healthz" >/dev/null 2>&1 && break
  sleep 1
done
curl -s -m 2 "http://localhost:${GATEWAY_PORT}/healthz" >/dev/null 2>&1 || { echo "[${LABEL}] FATAL: Gateway not healthy" >&2; exit 1; }

echo "[${LABEL}] preflight OK, running closed-model load (VUS=${VUS}, firstChunkDelayMs=${FIRST_CHUNK_DELAY_MS}, chunkIntervalMs=${CHUNK_INTERVAL_MS}, chunkCount=${CHUNK_COUNT})..."

# ---- Closed-model load (01-concurrent-connections.js) ----
MAX_DURATION_SEC=$(( (FIRST_CHUNK_DELAY_MS/1000) + (CHUNK_INTERVAL_MS*CHUNK_COUNT/1000) + 60 ))
"${K6_BIN}" run \
  --summary-export "${OUT_DIR}/k6-summary.json" \
  -e GATEWAY_URL="http://localhost:${GATEWAY_PORT}/chat/stream" \
  -e VUS="${VUS}" -e MAX_DURATION="${MAX_DURATION_SEC}s" \
  -e FIRST_CHUNK_DELAY_MS="${FIRST_CHUNK_DELAY_MS}" -e CHUNK_INTERVAL_MS="${CHUNK_INTERVAL_MS}" \
  -e CHUNK_COUNT="${CHUNK_COUNT}" \
  "${ROOT}/load-test-k6/scenarios/01-concurrent-connections.js" \
  > "${OUT_DIR}/k6.log" 2>&1
K6_EXIT=$?
echo "[${LABEL}] k6 exit=${K6_EXIT}"

echo "[${LABEL}] confirming drain..."
for i in $(seq 1 120); do
  ACTIVE=$(curl -s -m 3 "http://localhost:${GATEWAY_PORT}/metrics" | awk '/^gateway_async_active_requests /{print $2}')
  [ "${ACTIVE}" = "0.0" ] && break
  sleep 1
done

# ---- Dump the JFR recording BEFORE killing the JVM (StartFlightRecording
# writes the file continuously, but an explicit dump via jcmd ensures a
# clean, complete file rather than relying on JVM-exit flush timing) ----
JCMD_BIN="${JDK_HOME}/bin/jcmd"
"${JCMD_BIN}" "${GATEWAY_PID}" JFR.dump filename="${OUT_DIR}/recording.jfr" >> "${OUT_DIR}/gateway.log" 2>&1

# ---- Postflight ----
POSTFLIGHT_FAIL=""
ASYNC_ACTIVE=$(curl -s "http://localhost:${GATEWAY_PORT}/metrics" | awk '/^gateway_async_active_requests /{print $2}')
VT_ACTIVE=$(curl -s "http://localhost:${GATEWAY_PORT}/metrics" | awk '/^chat_virtual_tasks_active /{print $2}')
UNEXPECTED_ERR=$(curl -s "http://localhost:${GATEWAY_PORT}/metrics" | awk '/^gateway_unexpected_runtime_error_total /{print $2}')
[ "${ASYNC_ACTIVE}" = "0.0" ] || POSTFLIGHT_FAIL="${POSTFLIGHT_FAIL} async_active=${ASYNC_ACTIVE}"
[ "${VT_ACTIVE}" = "0.0" ] || POSTFLIGHT_FAIL="${POSTFLIGHT_FAIL} vt_active=${VT_ACTIVE}"
[ "${UNEXPECTED_ERR}" = "0.0" ] || POSTFLIGHT_FAIL="${POSTFLIGHT_FAIL} unexpected_runtime_error=${UNEXPECTED_ERR}"

# ---- JFR post-processing ----
"${JFR_BIN}" summary "${OUT_DIR}/recording.jfr" > "${OUT_DIR}/jfr-summary.txt" 2>&1
"${JFR_BIN}" print --events jdk.VirtualThreadPinned --stack-depth 64 "${OUT_DIR}/recording.jfr" > "${OUT_DIR}/virtual-thread-pinned-events.txt" 2>&1
cp "${JFC}" "${OUT_DIR}/phase2-vt-pinning.jfc"

python3 - "${OUT_DIR}" <<'PYEOF'
import re, json, sys, os
out_dir = sys.argv[1]
text = open(os.path.join(out_dir, "virtual-thread-pinned-events.txt")).read()
durations = [int(m) for m in re.findall(r"duration = (\d+) ms", text)]
summary = {
    "event_count": len(durations),
    "duration_min_ms": min(durations) if durations else None,
    "duration_max_ms": max(durations) if durations else None,
}
print(json.dumps(summary, indent=2))
with open(os.path.join(out_dir, "pinned-event-summary.json"), "w") as f:
    json.dump(summary, f, indent=2)
PYEOF

JDK_VERSION_OUTPUT=$("${JAVA_BIN}" -version 2>&1 | tr '\n' ' ' | sed 's/"/\\"/g')
K6_COMPLETED=$(python3 -c "
import json
try:
    d = json.load(open('${OUT_DIR}/k6-summary.json'))
    print(d['metrics'].get('client_stream_completed_total', {}).get('count', 0))
except Exception:
    print('null')
")
K6_FAILED=$(python3 -c "
import json
try:
    d = json.load(open('${OUT_DIR}/k6-summary.json'))
    print(d['metrics'].get('client_stream_failed_total', {}).get('count', 0))
except Exception:
    print('null')
")

cat > "${OUT_DIR}/environment.json" <<EOF
{
  "experiment": "${LABEL}",
  "purpose": "JFR jdk.VirtualThreadPinned diagnostic against the real Gateway (VIRTUAL_LIMITED, HttpURLConnection SSE relay) — not Formal, not performance data.",
  "thread_mode": "VIRTUAL_LIMITED",
  "vus": ${VUS},
  "workload": {"first_chunk_delay_ms": ${FIRST_CHUNK_DELAY_MS}, "chunk_interval_ms": ${CHUNK_INTERVAL_MS}, "chunk_count": ${CHUNK_COUNT}},
  "chat_read_timeout_ms": ${CHAT_READ_TIMEOUT_MS},
  "jfc_file": "phase2-vt-pinning.jfc",
  "jdk_version_output": "${JDK_VERSION_OUTPUT}",
  "host_arch": "$(uname -m)",
  "k6_exit_code": ${K6_EXIT},
  "client_stream_completed_total": ${K6_COMPLETED},
  "client_stream_failed_total": ${K6_FAILED},
  "postflight_fail": "${POSTFLIGHT_FAIL}"
}
EOF

if [ -n "${POSTFLIGHT_FAIL}" ]; then
  echo "[${LABEL}] WARNING: postflight not clean:${POSTFLIGHT_FAIL}" >&2
fi

echo "=== [${LABEL}] $(date -u +%Y-%m-%dT%H:%M:%SZ) done ==="
