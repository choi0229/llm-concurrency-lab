#!/usr/bin/env bash
# Phase 2 Unit 8 A — VT-Unlimited Screening / Downstream-bound Comparison
# (native macOS ARM64). Screening/diagnostic only — NOT Formal, no 3x
# repeats, not part of the Primary Formal dataset. Reuses the Formal
# harness's fresh-process/preflight/postflight/cleanup/RSS-sampler pattern,
# but skips Prometheus entirely (direct /metrics polling instead — screening
# runs are short enough that Prometheus's 5s scrape interval would be too
# coarse to catch peaks reliably) and uses the closed-model k6 scenario
# (01-concurrent-connections.js) instead of the Formal continuous scenario.
#
# Usage: ./scripts/run-phase2-native-screening.sh <label> <thread_mode> \
#   <vus> <mock_max_concurrent_processing> <mock_max_waiting> [permits]
#   thread_mode: VIRTUAL_UNLIMITED | VIRTUAL_LIMITED
#   permits: only used if thread_mode=VIRTUAL_LIMITED (default 50)
set -uo pipefail

LABEL="$1"; THREAD_MODE="$2"; VUS="$3"
MOCK_MAX_CONCURRENT="$4"; MOCK_MAX_WAITING="$5"
PERMITS="${6:-50}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${ROOT}/docs/test-results/phase2/secondary/vt-unlimited-screening/${LABEL}"
mkdir -p "${OUT_DIR}"
cd "${ROOT}"

NATIVE_DIR="${ROOT}/.native-runtime"
JDK_HOME="${NATIVE_DIR}/jdk-21.0.11+10/Contents/Home"
JAVA_BIN="${JDK_HOME}/bin/java"
K6_BIN="${NATIVE_DIR}/k6"
MOCK_VENV="${ROOT}/mock-llm-fastapi/.venv"
GATEWAY_PORT=8080
MOCK_PORT=8000
SAMPLE_INTERVAL_SEC=0.5

echo "=== [${LABEL}] $(date -u +%Y-%m-%dT%H:%M:%SZ) starting (THREAD_MODE=${THREAD_MODE}, VUS=${VUS}, mock_max_concurrent=${MOCK_MAX_CONCURRENT}, mock_max_waiting=${MOCK_MAX_WAITING}) ==="

for bin in "${JAVA_BIN}" "${K6_BIN}" "${MOCK_VENV}/bin/uvicorn"; do
  [ -x "${bin}" ] || { echo "[${LABEL}] FATAL: missing dependency ${bin}" >&2; exit 1; }
done

GATEWAY_PID=""; MOCK_PID=""; CAFFEINATE_PID=""; RSS_SAMPLER_PID=""; METRICS_SAMPLER_PID=""
cleanup() {
  echo "[${LABEL}] cleanup..."
  for pidvar in GATEWAY_PID MOCK_PID CAFFEINATE_PID RSS_SAMPLER_PID METRICS_SAMPLER_PID; do
    pid="${!pidvar}"
    if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null
      for i in $(seq 1 20); do kill -0 "${pid}" 2>/dev/null || break; sleep 0.5; done
      kill -0 "${pid}" 2>/dev/null && kill -9 "${pid}" 2>/dev/null
    fi
  done
}
trap cleanup EXIT

# ---- Preflight ----
PREFLIGHT_FAIL=""
pgrep -fq "Docker Desktop|com.docker.backend" && PREFLIGHT_FAIL="${PREFLIGHT_FAIL} docker_desktop_still_running"
for p in ${GATEWAY_PORT} ${MOCK_PORT}; do
  lsof -i ":${p}" -sTCP:LISTEN >/dev/null 2>&1 && PREFLIGHT_FAIL="${PREFLIGHT_FAIL} port_${p}_in_use"
done
if [ -n "${PREFLIGHT_FAIL}" ]; then
  echo "[${LABEL}] FATAL: preflight failed:${PREFLIGHT_FAIL}" >&2
  exit 1
fi

caffeinate -dims -w $$ &
CAFFEINATE_PID=$!

# ---- Fresh Mock LLM ----
MOCK_LLM_LOG_LEVEL=INFO MOCK_LLM_MAX_CONCURRENT_PROCESSING="${MOCK_MAX_CONCURRENT}" MOCK_LLM_MAX_WAITING="${MOCK_MAX_WAITING}" \
  "${MOCK_VENV}/bin/uvicorn" app.main:app --host 0.0.0.0 --port "${MOCK_PORT}" \
  --app-dir "${ROOT}/mock-llm-fastapi" \
  > "${OUT_DIR}/mock-llm.log" 2>&1 &
MOCK_PID=$!
for i in $(seq 1 30); do curl -s -m 2 "http://localhost:${MOCK_PORT}/healthz" >/dev/null 2>&1 && break; sleep 1; done
curl -s -m 2 "http://localhost:${MOCK_PORT}/healthz" >/dev/null 2>&1 || { echo "[${LABEL}] FATAL: Mock LLM not healthy" >&2; exit 1; }

# ---- Fresh Gateway JVM ----
GATEWAY_ENV=(THREAD_MODE="${THREAD_MODE}" MOCK_LLM_BASE_URL="http://localhost:${MOCK_PORT}"
  CHAT_CONNECT_TIMEOUT_MS=3000 CHAT_READ_TIMEOUT_MS=30000 CHAT_TOTAL_TIMEOUT_MS=60000)
[ "${THREAD_MODE}" = "VIRTUAL_LIMITED" ] && GATEWAY_ENV+=(CHAT_VT_LIMITED_PERMITS="${PERMITS}")
env "${GATEWAY_ENV[@]}" "${JAVA_BIN}" -jar "${ROOT}/gateway-mvc-java21/build/libs/gateway-mvc-java21-0.1.0.jar" \
  > "${OUT_DIR}/gateway.log" 2>&1 &
GATEWAY_PID=$!
for i in $(seq 1 30); do curl -s -m 2 "http://localhost:${GATEWAY_PORT}/healthz" >/dev/null 2>&1 && break; sleep 1; done
curl -s -m 2 "http://localhost:${GATEWAY_PORT}/healthz" >/dev/null 2>&1 || { echo "[${LABEL}] FATAL: Gateway not healthy" >&2; exit 1; }

CPU_START=$(curl -s "http://localhost:${GATEWAY_PORT}/metrics" | awk '/^process_cpu_seconds_total /{print $2}')
WALL_START=$(date +%s.%N)

# ---- RSS sampler (reused pattern from Formal harness) ----
RSS_CSV="${OUT_DIR}/native-rss-samples.csv"
echo "timestamp_epoch_s,rss_kib" > "${RSS_CSV}"
( while kill -0 "${GATEWAY_PID}" 2>/dev/null; do
    ts=$(date +%s)
    rss_kib=$(ps -o rss= -p "${GATEWAY_PID}" 2>/dev/null | tr -d ' ')
    [ -n "${rss_kib}" ] && echo "${ts},${rss_kib}" >> "${RSS_CSV}"
    sleep 1
  done ) &
RSS_SAMPLER_PID=$!

# ---- Metrics poller (direct /metrics polling, 0.5s — finer than a 5s
# Prometheus scrape, needed since these closed-model runs are short) ----
METRICS_CSV="${OUT_DIR}/gateway-metrics-samples.csv"
echo "timestamp_epoch_s,jvm_threads_current,chat_virtual_tasks_active,mockllm_current_concurrency,mockllm_waiting_requests" > "${METRICS_CSV}"
( while kill -0 "${GATEWAY_PID}" 2>/dev/null; do
    ts=$(date +%s.%N)
    gw=$(curl -s -m 1 "http://localhost:${GATEWAY_PORT}/metrics" 2>/dev/null)
    ml=$(curl -s -m 1 "http://localhost:${MOCK_PORT}/metrics" 2>/dev/null)
    threads=$(echo "${gw}" | awk '/^jvm_threads_current /{print $2}')
    vt_active=$(echo "${gw}" | awk '/^chat_virtual_tasks_active /{print $2}')
    mock_conc=$(echo "${ml}" | awk '/^mockllm_current_concurrency /{print $2}')
    mock_wait=$(echo "${ml}" | awk '/^mockllm_waiting_requests /{print $2}')
    echo "${ts},${threads:-},${vt_active:-},${mock_conc:-},${mock_wait:-}" >> "${METRICS_CSV}"
    sleep "${SAMPLE_INTERVAL_SEC}"
  done ) &
METRICS_SAMPLER_PID=$!

echo "[${LABEL}] preflight OK, running closed-model load..."

# ---- Closed-model load, standard mock workload (same as Formal) ----
"${K6_BIN}" run \
  --summary-export "${OUT_DIR}/k6-summary.json" \
  -e GATEWAY_URL="http://localhost:${GATEWAY_PORT}/chat/stream" \
  -e VUS="${VUS}" -e MAX_DURATION=120s \
  -e FIRST_CHUNK_DELAY_MS=1000 -e CHUNK_INTERVAL_MS=200 -e CHUNK_COUNT=35 \
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
sleep 3
WALL_END=$(date +%s.%N)
CPU_END=$(curl -s "http://localhost:${GATEWAY_PORT}/metrics" | awk '/^process_cpu_seconds_total /{print $2}')

# Stop samplers before reading final metrics/postflight
kill "${RSS_SAMPLER_PID}" "${METRICS_SAMPLER_PID}" 2>/dev/null
sleep 1
RSS_SAMPLER_PID=""; METRICS_SAMPLER_PID=""

# ---- Postflight ----
POSTFLIGHT_FAIL=""
ASYNC_ACTIVE=$(curl -s "http://localhost:${GATEWAY_PORT}/metrics" | awk '/^gateway_async_active_requests /{print $2}')
MOCK_CONC=$(curl -s "http://localhost:${MOCK_PORT}/metrics" | awk '/^mockllm_current_concurrency /{print $2}')
MOCK_WAIT=$(curl -s "http://localhost:${MOCK_PORT}/metrics" | awk '/^mockllm_waiting_requests /{print $2}')
UNEXPECTED_ERR=$(curl -s "http://localhost:${GATEWAY_PORT}/metrics" | awk '/^gateway_unexpected_runtime_error_total /{print $2}')
# NOTE: io.prometheus:simpleclient's text exposition format adds a trailing
# comma before the closing brace on labeled series
# (gateway_request_outcome_total{outcome="rejected",} 3.0) — confirmed via a
# live throwaway check. The pattern below matches that comma explicitly;
# without it this silently always read 0 regardless of actual rejections.
REJECTED_TOTAL=$(curl -s "http://localhost:${GATEWAY_PORT}/metrics" | awk -F'[ ]' '/^gateway_request_outcome_total\{outcome="rejected",\}/{print $NF}')
[ "${ASYNC_ACTIVE}" = "0.0" ] || POSTFLIGHT_FAIL="${POSTFLIGHT_FAIL} async_active=${ASYNC_ACTIVE}"
[ "${MOCK_CONC}" = "0.0" ] || POSTFLIGHT_FAIL="${POSTFLIGHT_FAIL} mock_concurrency=${MOCK_CONC}"
[ "${MOCK_WAIT}" = "0.0" ] || POSTFLIGHT_FAIL="${POSTFLIGHT_FAIL} mock_waiting=${MOCK_WAIT}"
[ "${UNEXPECTED_ERR}" = "0.0" ] || POSTFLIGHT_FAIL="${POSTFLIGHT_FAIL} unexpected_runtime_error=${UNEXPECTED_ERR}"

# ---- Aggregate result ----
python3 - "${OUT_DIR}" "${CPU_START}" "${CPU_END}" "${WALL_START}" "${WALL_END}" "${REJECTED_TOTAL:-0}" "${K6_EXIT}" "${POSTFLIGHT_FAIL}" "${LABEL}" "${THREAD_MODE}" "${VUS}" "${MOCK_MAX_CONCURRENT}" "${MOCK_MAX_WAITING}" <<'PYEOF'
import sys, json, csv, os

(out_dir, cpu_start, cpu_end, wall_start, wall_end, rejected_total, k6_exit,
 postflight_fail, label, thread_mode, vus, mock_max_concurrent, mock_max_waiting) = sys.argv[1:14]

def f(x, default=0.0):
    try:
        return float(x)
    except (ValueError, TypeError):
        return default

cpu_avg_cores = (f(cpu_end) - f(cpu_start)) / max(f(wall_end) - f(wall_start), 1e-9)

def read_rss(path):
    samples = []
    with open(path) as fh:
        r = csv.reader(fh)
        next(r, None)
        for row in r:
            if len(row) == 2:
                try:
                    samples.append(float(row[1]) * 1024)
                except ValueError:
                    pass
    return samples

def read_metrics(path):
    threads, vt_active, mock_conc, mock_wait = [], [], [], []
    with open(path) as fh:
        r = csv.reader(fh)
        next(r, None)
        for row in r:
            if len(row) != 5:
                continue
            _, t, v, mc, mw = row
            for lst, val in ((threads, t), (vt_active, v), (mock_conc, mc), (mock_wait, mw)):
                try:
                    lst.append(float(val))
                except ValueError:
                    pass
    return threads, vt_active, mock_conc, mock_wait

rss = read_rss(os.path.join(out_dir, "native-rss-samples.csv"))
threads, vt_active, mock_conc, mock_wait = read_metrics(os.path.join(out_dir, "gateway-metrics-samples.csv"))

with open(os.path.join(out_dir, "k6-summary.json")) as fh:
    k6 = json.load(fh)
completed = k6["metrics"].get("client_stream_completed_total", {}).get("count", 0)
failed = k6["metrics"].get("client_stream_failed_total", {}).get("count", 0)
ttfc = k6["metrics"].get("client_ttfc_seconds", {})
stream_dur = k6["metrics"].get("client_stream_duration_seconds", {})

result = {
    "label": label,
    "thread_mode": thread_mode,
    "vus": int(vus),
    "mock_max_concurrent_processing": int(mock_max_concurrent),
    "mock_max_waiting": int(mock_max_waiting),
    "k6_exit_code": int(k6_exit),
    "client_stream_completed_total": completed,
    "client_stream_failed_total": failed,
    "ttfc_seconds": {"avg": ttfc.get("avg"), "min": ttfc.get("min"), "max": ttfc.get("max")},
    "stream_duration_seconds": {"avg": stream_dur.get("avg"), "min": stream_dur.get("min"), "max": stream_dur.get("max")},
    "gateway_rejected_total": int(f(rejected_total)),
    "jvm_threads_current_peak": max(threads) if threads else None,
    "chat_virtual_tasks_active_peak": max(vt_active) if vt_active else None,
    "mockllm_current_concurrency_peak": max(mock_conc) if mock_conc else None,
    "mockllm_waiting_requests_peak": max(mock_wait) if mock_wait else None,
    "rss_start_bytes": rss[0] if rss else None,
    "rss_peak_bytes": max(rss) if rss else None,
    "cpu_avg_cores": cpu_avg_cores,
    "postflight_fail": postflight_fail,
}
with open(os.path.join(out_dir, "result.json"), "w") as fh:
    json.dump(result, fh, indent=2)
print(json.dumps(result, indent=2))
PYEOF

JDK_VERSION_OUTPUT=$("${JAVA_BIN}" -version 2>&1 | tr '\n' ' ' | sed 's/"/\\"/g')
cat > "${OUT_DIR}/environment.json" <<EOF
{
  "label": "${LABEL}",
  "thread_mode": "${THREAD_MODE}",
  "vus": ${VUS},
  "permits": $( [ "${THREAD_MODE}" = "VIRTUAL_LIMITED" ] && echo "${PERMITS}" || echo "null" ),
  "mock_max_concurrent_processing": ${MOCK_MAX_CONCURRENT},
  "mock_max_waiting": ${MOCK_MAX_WAITING},
  "jdk_version_output": "${JDK_VERSION_OUTPUT}",
  "host_arch": "$(uname -m)",
  "postflight_fail": "${POSTFLIGHT_FAIL}"
}
EOF

if [ -n "${POSTFLIGHT_FAIL}" ]; then
  echo "[${LABEL}] WARNING: postflight not clean:${POSTFLIGHT_FAIL}" >&2
fi

echo "=== [${LABEL}] $(date -u +%Y-%m-%dT%H:%M:%SZ) done ==="
