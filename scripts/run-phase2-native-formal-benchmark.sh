#!/usr/bin/env bash
# Phase 2 Unit 6 Formal Benchmark — Native macOS ARM64 harness (Docker-free).
#
# Separate from scripts/run-phase2-formal-benchmark.sh (the Docker harness,
# FROZEN — never modified by this file or its design). Reuses, unmodified:
#   - load-test-k6/scenarios/03-constant-arrival-rate-continuous.js
#   - scripts/collect_phase2_formal_result.py
#   - R3/R6/R8, warm-up 120s, measurement 300s, metric schema, client_cohort
#     semantics, measurement_window_completion_rate semantics.
# See docs/decisions/phase2-formal-native-macos-environment.md for the full
# design rationale (why native, CPU/heap policy, what's reused vs new).
# RSS is native-only (macOS `ps -o rss=` sampler on the Gateway PID) because
# process_resident_memory_bytes is Linux-/proc-only in the Prometheus Java
# client and isn't exposed on macOS — see the ADR's RSS policy section.
#
# Runtime deps (JDK/Prometheus/k6/mock venv) come from
# scripts/setup-phase2-native-runtime.sh — run that first.
#
# Usage: ./scripts/run-phase2-native-formal-benchmark.sh <config_label> \
#   <thread_mode> <load_label> <rate> <warmup_sec> <measurement_sec> \
#   <run_number> [pre_allocated_vus]
set -uo pipefail  # NOT -e: cleanup() must run on every exit path, including
                   # a failing command mid-script — see trap below.

CONFIG_LABEL="$1"; THREAD_MODE="$2"; LOAD_LABEL="$3"; RATE="$4"
WARMUP_SEC="$5"; MEASUREMENT_SEC="$6"; RUN_NUMBER="$7"
PRE_ALLOCATED_VUS="${8:-150}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_ID="${CONFIG_LABEL}-${LOAD_LABEL}-run${RUN_NUMBER}"
OUT_DIR="${ROOT}/docs/test-results/phase2/unit6-native/${RUN_ID}"
mkdir -p "${OUT_DIR}"
cd "${ROOT}"

NATIVE_DIR="${ROOT}/.native-runtime"
JDK_HOME="${NATIVE_DIR}/jdk-21.0.11+10/Contents/Home"
JAVA_BIN="${JDK_HOME}/bin/java"
K6_BIN="${NATIVE_DIR}/k6"
PROM_BIN="${NATIVE_DIR}/prometheus-3.13.2.darwin-arm64/prometheus"
MOCK_VENV="${ROOT}/mock-llm-fastapi/.venv"
PROM_PORT=9092
GATEWAY_PORT=8080
MOCK_PORT=8000
# RSS sampling interval — fixed across ALL native runs (P-E and VT-Limited
# alike), see docs/decisions/phase2-formal-native-macos-environment.md
# section on RSS policy. process_resident_memory_bytes is Linux-only in the
# Prometheus Java client's StandardExports, so native RSS comes from a
# macOS `ps -o rss=` sampler instead (CPU/threads/heap/GC stay
# Prometheus-authoritative, unaffected by this).
RSS_SAMPLE_INTERVAL_SEC=1

echo "=== [${RUN_ID}] $(date -u +%Y-%m-%dT%H:%M:%SZ) starting (NATIVE, THREAD_MODE=${THREAD_MODE}) ==="

for bin in "${JAVA_BIN}" "${K6_BIN}" "${PROM_BIN}" "${MOCK_VENV}/bin/uvicorn"; do
  if [ ! -x "${bin}" ]; then
    echo "[${RUN_ID}] FATAL: missing runtime dependency: ${bin} — run scripts/setup-phase2-native-runtime.sh first" >&2
    exit 1
  fi
done

# ---- PID lifecycle: cleanup() runs on ANY exit (success, failure, Ctrl-C) so
# no stale process/port survives this script — item 8 of the Native Formal
# design review. ----
GATEWAY_PID=""; MOCK_PID=""; PROM_PID=""; CAFFEINATE_PID=""; RSS_SAMPLER_PID=""; PROM_TSDB_DIR=""
cleanup() {
  echo "[${RUN_ID}] cleanup: stopping native processes..."
  for pidvar in GATEWAY_PID MOCK_PID PROM_PID CAFFEINATE_PID RSS_SAMPLER_PID; do
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
  rm -f "${OUT_DIR}/gateway.pid" "${OUT_DIR}/mock.pid" "${OUT_DIR}/prometheus.pid"
  # Scratch TSDB dir, not a result artifact — result.json/logs/CSVs under
  # OUT_DIR are untouched. Removed here (after Prometheus is confirmed
  # stopped above) so repeated runs don't accumulate mktemp dirs under
  # $TMPDIR indefinitely across an 18-run matrix. Runs on every exit path
  # (success, failure, Ctrl-C) since this is inside the EXIT trap.
  [ -n "${PROM_TSDB_DIR}" ] && rm -rf "${PROM_TSDB_DIR}"
}
trap cleanup EXIT

# ---- Native Preflight (item 9) ----
PREFLIGHT_FAIL=""

# Docker Desktop must be fully quit — the whole point of Native Formal is to
# not share this host with the Docker Desktop VM that caused the original
# Mac-environment rejection (docs/decisions/phase2-formal-linux-environment.md).
# This script only CHECKS and reports — it never kills Docker Desktop itself;
# the operator quits it manually (Quit, not just idle) before running this.
if pgrep -fq "Docker Desktop|com.docker.backend"; then
  PREFLIGHT_FAIL="${PREFLIGHT_FAIL} docker_desktop_still_running"
fi
DOCKER_DESKTOP_RUNNING=$(pgrep -f "Docker Desktop|com.docker.backend" >/dev/null 2>&1 && echo true || echo false)

# Ports must be free before we bind them.
for p in ${GATEWAY_PORT} ${MOCK_PORT} ${PROM_PORT}; do
  if lsof -i ":${p}" -sTCP:LISTEN >/dev/null 2>&1; then
    PREFLIGHT_FAIL="${PREFLIGHT_FAIL} port_${p}_in_use"
  fi
done

# Stale PID files from a previous incomplete run.
for f in "${OUT_DIR}/gateway.pid" "${OUT_DIR}/mock.pid" "${OUT_DIR}/prometheus.pid"; do
  if [ -f "$f" ] && kill -0 "$(cat "$f")" 2>/dev/null; then
    PREFLIGHT_FAIL="${PREFLIGHT_FAIL} stale_pid_$(basename "$f")"
  fi
done

if [ -n "${PREFLIGHT_FAIL}" ]; then
  echo "[${RUN_ID}] FATAL: native preflight failed:${PREFLIGHT_FAIL}" >&2
  echo "[${RUN_ID}] If docker_desktop_still_running: quit Docker Desktop manually (Docker icon -> Quit Docker Desktop), not just close its window." >&2
  exit 1
fi

# Keep this host awake for the whole run (self-contained — not relying on an
# operator-supplied caffeinate wrapper). -w $$ ties it to this script's PID
# so it releases automatically when the script exits either way.
caffeinate -dims -w $$ &
CAFFEINATE_PID=$!

HOST_ARCH=$(uname -m)
POWER_SOURCE=$(pmset -g batt 2>&1 | head -1)
AVAILABLE_PROCESSORS=$("${JAVA_BIN}" -cp "${NATIVE_DIR}/tools" AvailableProcessors)
# The JDK tarball is deleted by setup-phase2-native-runtime.sh after its
# checksum gate passes (see that script) — not re-verified per run here.
# Pinned value: 6ebcf221c9b41507b14c098e93c6ead6440b8d9bd154f8ec666c4c73abbdb201
# (OpenJDK21U-jdk_aarch64_mac_hotspot_21.0.11_10.tar.gz), same trust model as
# the Docker harness's FROM digest (verified once, not re-hashed per run).
JDK_VERSION_OUTPUT=$("${JAVA_BIN}" -version 2>&1 | tr '\n' ' ')
K6_VERSION_OUTPUT=$("${K6_BIN}" version 2>&1)
MOCK_VERSIONS=$("${MOCK_VENV}/bin/pip" freeze 2>/dev/null | grep -iE "^fastapi|^uvicorn|^prometheus_client|^pydantic==")
# No GNU `timeout` on stock macOS (no coreutils) — bound sntp manually so a
# blocked network can't hang preflight.
if command -v sntp >/dev/null 2>&1; then
  SNTP_OUT_FILE=$(mktemp "${TMPDIR:-/tmp}/phase2-native-sntp-XXXXXX")
  sntp -sS time.apple.com > "${SNTP_OUT_FILE}" 2>&1 &
  SNTP_PID=$!
  for i in $(seq 1 5); do
    kill -0 "${SNTP_PID}" 2>/dev/null || break
    sleep 1
  done
  kill -0 "${SNTP_PID}" 2>/dev/null && kill -9 "${SNTP_PID}" 2>/dev/null
  CLOCK_CHECK=$(cat "${SNTP_OUT_FILE}" 2>/dev/null)
  rm -f "${SNTP_OUT_FILE}"
  [ -z "${CLOCK_CHECK}" ] && CLOCK_CHECK="sntp unavailable/blocked"
else
  CLOCK_CHECK="sntp unavailable/blocked"
fi
HEAP_ERGONOMICS=$("${JAVA_BIN}" -XX:+PrintFlagsFinal -version 2>/dev/null | grep -E "MaxHeapSize|InitialHeapSize" | tr -s ' ')

echo "[${RUN_ID}] preflight OK — arch=${HOST_ARCH} availableProcessors=${AVAILABLE_PROCESSORS} docker_desktop_running=${DOCKER_DESKTOP_RUNNING}"
echo "[${RUN_ID}] power: ${POWER_SOURCE}"
echo "[${RUN_ID}] clock check: ${CLOCK_CHECK}"

# ---- Fresh Prometheus (fresh TSDB dir every run — no --renew-anon-volumes
# equivalent needed since there's no Docker volume, but the same "no stale
# data from a prior run" guarantee is required) ----
PROM_TSDB_DIR=$(mktemp -d "${TMPDIR:-/tmp}/phase2-native-prom-XXXXXX")
"${PROM_BIN}" \
  --config.file="${ROOT}/monitoring/prometheus/prometheus-native.yml" \
  --storage.tsdb.path="${PROM_TSDB_DIR}" \
  --web.listen-address=":${PROM_PORT}" \
  > "${OUT_DIR}/prometheus.log" 2>&1 &
PROM_PID=$!
echo "${PROM_PID}" > "${OUT_DIR}/prometheus.pid"
for i in $(seq 1 30); do
  curl -s -m 2 "http://localhost:${PROM_PORT}/-/ready" >/dev/null 2>&1 && break
  sleep 1
done
curl -s -m 2 "http://localhost:${PROM_PORT}/-/ready" >/dev/null 2>&1 || { echo "[${RUN_ID}] FATAL: Prometheus did not become ready" >&2; exit 1; }

# ---- Fresh Mock LLM ----
MOCK_LLM_LOG_LEVEL=INFO MOCK_LLM_MAX_CONCURRENT_PROCESSING=0 MOCK_LLM_MAX_WAITING=0 \
  "${MOCK_VENV}/bin/uvicorn" app.main:app --host 0.0.0.0 --port "${MOCK_PORT}" \
  --app-dir "${ROOT}/mock-llm-fastapi" \
  > "${OUT_DIR}/mock-llm.log" 2>&1 &
MOCK_PID=$!
echo "${MOCK_PID}" > "${OUT_DIR}/mock.pid"
for i in $(seq 1 30); do
  curl -s -m 2 "http://localhost:${MOCK_PORT}/healthz" >/dev/null 2>&1 && break
  sleep 1
done
curl -s -m 2 "http://localhost:${MOCK_PORT}/healthz" >/dev/null 2>&1 || { echo "[${RUN_ID}] FATAL: Mock LLM did not become healthy" >&2; exit 1; }

# ---- Fresh Gateway JVM ----
# No -Xmx/-Xms (default ergonomics, identical for P-E and VT-Limited on this
# host — see decision doc section on heap policy) and no
# -Djdk.virtualThreadScheduler.parallelism override (both configs get full
# host CPU access — see decision doc section on CPU policy).
GATEWAY_ENV=(THREAD_MODE="${THREAD_MODE}" MOCK_LLM_BASE_URL="http://localhost:${MOCK_PORT}"
  CHAT_CONNECT_TIMEOUT_MS=3000 CHAT_READ_TIMEOUT_MS=30000 CHAT_TOTAL_TIMEOUT_MS=60000)
if [ "${THREAD_MODE}" = "VIRTUAL_LIMITED" ]; then
  GATEWAY_ENV+=(CHAT_VT_LIMITED_PERMITS=50)
fi
env "${GATEWAY_ENV[@]}" "${JAVA_BIN}" -jar "${ROOT}/gateway-mvc-java21/build/libs/gateway-mvc-java21-0.1.0.jar" \
  > "${OUT_DIR}/gateway.log" 2>&1 &
GATEWAY_PID=$!
echo "${GATEWAY_PID}" > "${OUT_DIR}/gateway.pid"
for i in $(seq 1 30); do
  curl -s -m 2 "http://localhost:${GATEWAY_PORT}/healthz" >/dev/null 2>&1 && break
  sleep 1
done
curl -s -m 2 "http://localhost:${GATEWAY_PORT}/healthz" >/dev/null 2>&1 || { echo "[${RUN_ID}] FATAL: Gateway did not become healthy" >&2; exit 1; }

# Poll instead of a single fixed sleep — Prometheus's first scrape of each
# static target is jittered within scrape_interval from when its config
# loaded (before mock/gateway were necessarily up), so a one-shot check after
# a fixed sleep can race a target's first successful scrape (observed: gw=1
# but ml=0 after a 6s sleep with scrape_interval=5s). Poll up to 20s.
PROM_UP_GW=0; PROM_UP_ML=0
for i in $(seq 1 20); do
  PROM_UP_GW=$(curl -s --data-urlencode 'query=up{job="gateway-mvc-java21"}' "http://localhost:${PROM_PORT}/api/v1/query" | python3 -c "import json,sys;d=json.load(sys.stdin);r=d['data']['result'];print(r[0]['value'][1] if r else '0')")
  PROM_UP_ML=$(curl -s --data-urlencode 'query=up{job="mock-llm-fastapi"}' "http://localhost:${PROM_PORT}/api/v1/query" | python3 -c "import json,sys;d=json.load(sys.stdin);r=d['data']['result'];print(r[0]['value'][1] if r else '0')")
  [ "${PROM_UP_GW}" = "1" ] && [ "${PROM_UP_ML}" = "1" ] && break
  sleep 1
done
if [ "${PROM_UP_GW}" != "1" ] || [ "${PROM_UP_ML}" != "1" ]; then
  echo "[${RUN_ID}] FATAL: prometheus not scraping targets (gw=${PROM_UP_GW} mock=${PROM_UP_ML})" >&2
  exit 1
fi

# ---- Mode-aware Preflight (idle-state check, same as Docker harness) ----
ASYNC_ACTIVE=$(curl -s "http://localhost:${GATEWAY_PORT}/metrics" | awk '/^gateway_async_active_requests /{print $2}')
MOCK_CONC=$(curl -s "http://localhost:${MOCK_PORT}/metrics" | awk '/^mockllm_current_concurrency /{print $2}')
MOCK_WAIT=$(curl -s "http://localhost:${MOCK_PORT}/metrics" | awk '/^mockllm_waiting_requests /{print $2}')
IDLE_FAIL=""
[ "${ASYNC_ACTIVE}" = "0.0" ] || IDLE_FAIL="${IDLE_FAIL} async_active=${ASYNC_ACTIVE}"
[ "${MOCK_CONC}" = "0.0" ] || IDLE_FAIL="${IDLE_FAIL} mock_concurrency=${MOCK_CONC}"
[ "${MOCK_WAIT}" = "0.0" ] || IDLE_FAIL="${IDLE_FAIL} mock_waiting=${MOCK_WAIT}"
if [ "${THREAD_MODE}" = "PLATFORM" ]; then
  EXEC_ACTIVE=$(curl -s "http://localhost:${GATEWAY_PORT}/metrics" | awk '/^gateway_executor_active_threads /{print $2}')
  [ "${EXEC_ACTIVE}" = "0.0" ] || IDLE_FAIL="${IDLE_FAIL} executor_active=${EXEC_ACTIVE}"
elif [ "${THREAD_MODE}" = "VIRTUAL_LIMITED" ]; then
  VT_ACTIVE=$(curl -s "http://localhost:${GATEWAY_PORT}/metrics" | awk '/^chat_virtual_tasks_active /{print $2}')
  [ "${VT_ACTIVE}" = "0.0" ] || IDLE_FAIL="${IDLE_FAIL} vt_active=${VT_ACTIVE}"
  PERMITS=$(curl -s "http://localhost:${GATEWAY_PORT}/debug/permits" | python3 -c "import json,sys; print(json.load(sys.stdin).get('availablePermits'))" 2>/dev/null || echo "?")
  [ "${PERMITS}" = "50" ] || IDLE_FAIL="${IDLE_FAIL} available_permits=${PERMITS}(expected 50)"
fi
if [ -n "${IDLE_FAIL}" ]; then
  echo "[${RUN_ID}] FATAL: preflight not idle:${IDLE_FAIL}" >&2
  exit 1
fi
echo "[${RUN_ID}] preflight OK (native, mode=${THREAD_MODE})"

# ---- Native RSS sampler (macOS ps -o rss=, Gateway JVM PID only) ----
# Started here (Gateway healthy, before k6/warm-up starts) so it covers
# warm-up -> measurement -> drain continuously — measurement_start/end are
# computed AFTER k6 exits (below) and used to filter this raw CSV
# post-hoc, never to gate when sampling starts/stops.
RSS_CSV="${OUT_DIR}/native-rss-samples.csv"
echo "timestamp_epoch_s,rss_kib" > "${RSS_CSV}"
(
  while kill -0 "${GATEWAY_PID}" 2>/dev/null; do
    ts=$(date +%s)
    rss_kib=$(ps -o rss= -p "${GATEWAY_PID}" 2>/dev/null | tr -d ' ')
    [ -n "${rss_kib}" ] && echo "${ts},${rss_kib}" >> "${RSS_CSV}"
    sleep "${RSS_SAMPLE_INTERVAL_SEC}"
  done
) &
RSS_SAMPLER_PID=$!
echo "[${RUN_ID}] RSS sampler started (pid=${RSS_SAMPLER_PID}, interval=${RSS_SAMPLE_INTERVAL_SEC}s)"

# ---- Continuous warm-up -> measurement, single k6 process (identical
# lifecycle to the Docker harness, see docs/decisions/
# phase2-formal-linux-environment.md item 2/3 and docs/decisions/
# phase2-formal-native-macos-environment.md) ----
"${K6_BIN}" run \
  --summary-trend-stats "p(50),p(95),p(99),min,max" \
  --summary-export "${OUT_DIR}/k6-summary.json" \
  -e GATEWAY_URL="http://localhost:${GATEWAY_PORT}/chat/stream" \
  -e RATE="${RATE}" -e WARMUP_SEC="${WARMUP_SEC}" -e MEASUREMENT_SEC="${MEASUREMENT_SEC}" \
  -e GRACEFUL_STOP=90s \
  -e PRE_ALLOCATED_VUS="${PRE_ALLOCATED_VUS}" -e MAX_VUS=1000 \
  "${ROOT}/load-test-k6/scenarios/03-constant-arrival-rate-continuous.js" \
  > "${OUT_DIR}/measurement-k6.log" 2>&1
K6_EXIT=$?
[ "${K6_EXIT}" -ne 0 ] && echo "[${RUN_ID}] k6 run exited non-zero (${K6_EXIT}, logged)"

# Single source of truth for the phase boundary — read back from k6's own
# setup() (see 03-constant-arrival-rate-continuous.js), not recomputed here.
MEASUREMENT_START=$(python3 -c "
import json, sys
try:
    with open('${OUT_DIR}/k6-summary.json') as f:
        d = json.load(f)
    print(d['metrics']['measurement_start_epoch_s']['value'])
except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
    print(f'ERROR: could not read measurement_start_epoch_s: {e}', file=sys.stderr)
    sys.exit(1)
")
if [ -z "${MEASUREMENT_START}" ]; then
  echo "[${RUN_ID}] FATAL: k6 did not emit measurement_start_epoch_s" >&2
  exit 1
fi
MEASUREMENT_END=$(python3 -c "print(${MEASUREMENT_START} + ${MEASUREMENT_SEC})")

echo "[${RUN_ID}] k6 process exited, confirming drain..."
for i in $(seq 1 120); do
  ACTIVE=$(curl -s -m 3 "http://localhost:${GATEWAY_PORT}/metrics" | awk '/^gateway_async_active_requests /{print $2}')
  [ "${ACTIVE}" = "0.0" ] && break
  sleep 1
done
sleep 8
DRAIN_END=$(date +%s)

# ---- Stop RSS sampler now that drain is confirmed complete (samples span
# warm-up -> measurement -> drain, per design) — before Postflight so the
# postflight check below can verify it actually stopped. ----
if [ -n "${RSS_SAMPLER_PID}" ]; then
  kill "${RSS_SAMPLER_PID}" 2>/dev/null
  for i in $(seq 1 10); do
    kill -0 "${RSS_SAMPLER_PID}" 2>/dev/null || break
    sleep 0.5
  done
  kill -0 "${RSS_SAMPLER_PID}" 2>/dev/null && kill -9 "${RSS_SAMPLER_PID}" 2>/dev/null
fi
RSS_SAMPLER_STILL_ALIVE=$(kill -0 "${RSS_SAMPLER_PID}" 2>/dev/null && echo true || echo false)

# ---- Counter-plateau evidence (Formal integrity check, 2026-08-19) ----
# Native Stability Canary showed that a log-gap alone isn't sufficient
# evidence of environment health — the Mac Docker Desktop stall history was
# confirmed by two INDEPENDENT signals agreeing (log gap + Prometheus counter
# plateau). This captures that second signal automatically for every Formal
# run, WHILE Prometheus is still alive and before its scratch TSDB is deleted
# (see cleanup() at EXIT) — order is: drain confirmed -> this plateau query
# -> counter-plateau-check.json saved -> collect_phase2_formal_result.py runs
# -> (later, at script exit) Prometheus stops -> TSDB removed. Window is the
# new-arrival-active span only (warm-up start through measurement_end) —
# drain's expected counter-leveling is deliberately excluded, not a plateau.
# 20s threshold matches the existing stall_check.json log-gap threshold
# (same Formal-integrity bar, not a new arbitrary number) — see ADR: Canary
# clean run showed ~5s max, the Mac Docker rejected environment showed
# 30-35s.
WARMUP_START=$(python3 -c "print(${MEASUREMENT_START} - ${WARMUP_SEC})")
python3 - "${WARMUP_START}" "${MEASUREMENT_END}" "${PROM_PORT}" "${OUT_DIR}/counter-plateau-check.json" <<'PYEOF'
import sys, json, urllib.request, urllib.parse

window_start, window_end, prom_port, out_path = sys.argv[1:5]
window_start = float(window_start)
window_end = float(window_end)

def query_range(metric):
    params = urllib.parse.urlencode({
        "query": metric, "start": window_start, "end": window_end, "step": "5"
    })
    url = f"http://localhost:{prom_port}/api/v1/query_range?{params}"
    with urllib.request.urlopen(url, timeout=15) as r:
        d = json.load(r)
    return d.get("data", {}).get("result", [])

def max_plateau_seconds(values):
    # Longest time span over which a counter's value never increased —
    # values are (ts, val) pairs sorted by ts.
    if len(values) < 2:
        return 0.0
    max_span = 0.0
    run_start_ts = values[0][0]
    prev_val = values[0][1]
    for ts, val in values[1:]:
        if val == prev_val:
            span = ts - run_start_ts
            if span > max_span:
                max_span = span
        else:
            run_start_ts = ts
        prev_val = val
    return max_span

result = {
    "window_start_epoch_s": window_start,
    "window_end_epoch_s": window_end,
    "window_note": "new-arrival-active window only: measurement_start-warmup_sec through measurement_end. Drain-phase counter leveling is intentionally excluded, not treated as a plateau.",
    "step_seconds": 5,
    "query_ok": True,
}

try:
    gw_series = query_range("gateway_request_received_total")
    ml_series = query_range("mockllm_completed_requests_total")
    if not gw_series or not ml_series:
        result["query_ok"] = False
        result["query_error"] = "no data returned for one or both metrics"
    else:
        gw_values = [(float(ts), float(v)) for ts, v in gw_series[0]["values"]]
        ml_values = [(float(ts), float(v)) for ts, v in ml_series[0]["values"]]
        result["gateway_sample_count"] = len(gw_values)
        result["mock_sample_count"] = len(ml_values)
        result["gateway_max_plateau_seconds"] = max_plateau_seconds(gw_values)
        result["mock_max_plateau_seconds"] = max_plateau_seconds(ml_values)
except Exception as e:
    result["query_ok"] = False
    result["query_error"] = str(e)

gw_plateau = result.get("gateway_max_plateau_seconds")
ml_plateau = result.get("mock_max_plateau_seconds")
result["plateau_over_20s"] = (not result["query_ok"]) or (
    (gw_plateau is not None and gw_plateau > 20.0)
    or (ml_plateau is not None and ml_plateau > 20.0)
)

with open(out_path, "w") as f:
    json.dump(result, f, indent=2)
print(json.dumps(result, indent=2))
PYEOF
echo "[${RUN_ID}] counter-plateau-check.json saved"

# ---- Postflight ----
POSTFLIGHT_FAIL=""
ASYNC_ACTIVE=$(curl -s "http://localhost:${GATEWAY_PORT}/metrics" | awk '/^gateway_async_active_requests /{print $2}')
MOCK_CONC=$(curl -s "http://localhost:${MOCK_PORT}/metrics" | awk '/^mockllm_current_concurrency /{print $2}')
MOCK_WAIT=$(curl -s "http://localhost:${MOCK_PORT}/metrics" | awk '/^mockllm_waiting_requests /{print $2}')
UNEXPECTED_ERR=$(curl -s "http://localhost:${GATEWAY_PORT}/metrics" | awk '/^gateway_unexpected_runtime_error_total /{print $2}')
[ "${ASYNC_ACTIVE}" = "0.0" ] || POSTFLIGHT_FAIL="${POSTFLIGHT_FAIL} async_active=${ASYNC_ACTIVE}"
[ "${MOCK_CONC}" = "0.0" ] || POSTFLIGHT_FAIL="${POSTFLIGHT_FAIL} mock_concurrency=${MOCK_CONC}"
[ "${MOCK_WAIT}" = "0.0" ] || POSTFLIGHT_FAIL="${POSTFLIGHT_FAIL} mock_waiting=${MOCK_WAIT}"
[ "${UNEXPECTED_ERR}" = "0.0" ] || POSTFLIGHT_FAIL="${POSTFLIGHT_FAIL} unexpected_runtime_error=${UNEXPECTED_ERR}"
[ "${RSS_SAMPLER_STILL_ALIVE}" = "false" ] || POSTFLIGHT_FAIL="${POSTFLIGHT_FAIL} rss_sampler_still_alive"
if [ "${THREAD_MODE}" = "PLATFORM" ]; then
  EXEC_ACTIVE=$(curl -s "http://localhost:${GATEWAY_PORT}/metrics" | awk '/^gateway_executor_active_threads /{print $2}')
  [ "${EXEC_ACTIVE}" = "0.0" ] || POSTFLIGHT_FAIL="${POSTFLIGHT_FAIL} executor_active=${EXEC_ACTIVE}"
elif [ "${THREAD_MODE}" = "VIRTUAL_LIMITED" ]; then
  VT_ACTIVE=$(curl -s "http://localhost:${GATEWAY_PORT}/metrics" | awk '/^chat_virtual_tasks_active /{print $2}')
  [ "${VT_ACTIVE}" = "0.0" ] || POSTFLIGHT_FAIL="${POSTFLIGHT_FAIL} vt_active=${VT_ACTIVE}"
fi

cat > "${OUT_DIR}/timestamps.json" <<EOF
{
  "measurement_start": ${MEASUREMENT_START},
  "measurement_end": ${MEASUREMENT_END},
  "drain_end": ${DRAIN_END},
  "lifecycle_note": "Native macOS harness — continuous warm-up->measurement, single k6 process, no drain between them. measurement_start read back from k6's own setup()-emitted measurement_start_epoch_s Gauge (same single-source-of-truth mechanism as the Docker harness). Host clock only (no second/Docker-VM clock domain to compare against natively)."
}
EOF

cat > "${OUT_DIR}/environment.json" <<EOF
{
  "environment": "native-macos-arm64",
  "configuration": "${CONFIG_LABEL}",
  "thread_mode": "${THREAD_MODE}",
  "load": "${LOAD_LABEL}",
  "run_number": ${RUN_NUMBER},
  "arrival_rate_target": ${RATE},
  "k6_pre_allocated_vus": ${PRE_ALLOCATED_VUS},
  "warmup_duration_s": ${WARMUP_SEC},
  "measurement_duration_s": ${MEASUREMENT_SEC},
  "mock_llm_workload": {"first_chunk_delay_ms": 1000, "chunk_interval_ms": 200, "chunk_count": 35, "chunk_size_bytes": 64},
  "timeouts_ms": {"connect": 3000, "read": 30000, "total": 60000},
  "resource_policy": {
    "note": "No Docker cgroup CPU/memory limits on native macOS — see docs/decisions/phase2-formal-native-macos-environment.md. Both P-E and VT-Limited get unrestricted host CPU (no jdk.virtualThreadScheduler.parallelism override) and identical default JVM heap ergonomics (no -Xmx/-Xms).",
    "available_processors": ${AVAILABLE_PROCESSORS},
    "heap_ergonomics": "$(echo "${HEAP_ERGONOMICS}" | tr '\n' ';' | sed 's/"/\\"/g')"
  },
  "host": {
    "arch": "${HOST_ARCH}",
    "power_source": "$(echo "${POWER_SOURCE}" | sed 's/"/\\"/g')",
    "docker_desktop_running": ${DOCKER_DESKTOP_RUNNING},
    "clock_check": "$(echo "${CLOCK_CHECK}" | tr '\n' ' ' | sed 's/"/\\"/g')"
  },
  "k6_version": "$(echo "${K6_VERSION_OUTPUT}" | tr '\n' ' ' | sed 's/"/\\"/g')",
  "mock_versions": "$(echo "${MOCK_VERSIONS}" | tr '\n' ' ' | sed 's/"/\\"/g')",
  "jdk": "Eclipse Temurin 21.0.11+10-LTS (macOS aarch64)",
  "jdk_version_output": "$(echo "${JDK_VERSION_OUTPUT}" | sed 's/"/\\"/g')",
  "jdk_artifact": "OpenJDK21U-jdk_aarch64_mac_hotspot_21.0.11_10.tar.gz",
  "jdk_sha256_pinned": "6ebcf221c9b41507b14c098e93c6ead6440b8d9bd154f8ec666c4c73abbdb201",
  "spring_boot": "4.1.0",
  "spring_framework": "7.0.8",
  "postflight_fail": "${POSTFLIGHT_FAIL}"
}
EOF

if [ -n "${POSTFLIGHT_FAIL}" ]; then
  echo "[${RUN_ID}] WARNING: postflight NOT clean:${POSTFLIGHT_FAIL} — run flagged, not auto-discarded" >&2
fi

# ---- Stall detection (same combined-judgment approach as the Docker
# harness — log gap is diagnostic, not the sole basis for invalidation) ----
python3 - "${OUT_DIR}/gateway.log" "${OUT_DIR}/mock-llm.log" "${OUT_DIR}/stall_check.json" <<'PYEOF'
import sys, json, re, datetime
gw_log, ml_log, out_path = sys.argv[1:4]

def gaps(path, threshold=20):
    times = []
    pat = re.compile(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})')
    try:
        with open(path) as f:
            for line in f:
                m = pat.match(line)
                if m:
                    times.append(datetime.datetime.fromisoformat(m.group(1)))
    except FileNotFoundError:
        return []
    times = sorted(set(times))
    found = []
    prev = None
    for t in times:
        if prev and (t - prev).total_seconds() > threshold:
            found.append({"gap_start": prev.isoformat(), "gap_end": t.isoformat(),
                          "gap_seconds": (t - prev).total_seconds()})
        prev = t
    return found

gw_gaps = gaps(gw_log)
ml_gaps = gaps(ml_log)
result = {
    "gateway_log_gaps_over_20s": gw_gaps,
    "mock_llm_log_gaps_over_20s": ml_gaps,
    "note": "A gap here does not by itself invalidate the run — check client_cohort.invariant_ok and whether the gap falls inside [measurement_start, measurement_end] before judging impact on measurement_window_completion_rate.",
}
with open(out_path, "w") as f:
    json.dump(result, f, indent=2)
if gw_gaps or ml_gaps:
    print(f"[stall_check] WARNING: {len(gw_gaps)} gateway gap(s), {len(ml_gaps)} mock-llm gap(s) >20s — see {out_path}")
else:
    print("[stall_check] no gaps >20s detected")
PYEOF

python3 "${ROOT}/scripts/collect_phase2_formal_result.py" "${CONFIG_LABEL}" "${THREAD_MODE}" "${LOAD_LABEL}" "${RUN_NUMBER}" \
  "${MEASUREMENT_START}" "${MEASUREMENT_END}" "${DRAIN_END}" "${RATE}" "${OUT_DIR}/k6-summary.json" "http://localhost:${PROM_PORT}" \
  | tee "${OUT_DIR}/result.json"

# ---- Native RSS: filter the raw ps sampler CSV down to the measurement
# window and merge as a separate "native_resource" block. Does NOT touch
# collect_phase2_formal_result.py or its schema (frozen, shared with the
# Docker harness) — this is native-only, added on top. ----
python3 - "${RSS_CSV}" "${MEASUREMENT_START}" "${MEASUREMENT_END}" "${OUT_DIR}/result.json" "${RSS_SAMPLE_INTERVAL_SEC}" <<'PYEOF'
import sys, csv, json

csv_path, meas_start, meas_end, result_path, interval = sys.argv[1:6]
meas_start = float(meas_start)
meas_end = float(meas_end)

samples = []
with open(csv_path) as f:
    reader = csv.reader(f)
    next(reader, None)  # header
    for row in reader:
        if len(row) != 2:
            continue
        try:
            samples.append((float(row[0]), float(row[1])))
        except ValueError:
            continue
samples.sort(key=lambda s: s[0])

start_sample = next((s for s in samples if s[0] >= meas_start), None)
window_samples = [s for s in samples if meas_start <= s[0] <= meas_end]

rss_start_bytes = start_sample[1] * 1024 if start_sample else None
rss_peak_kib = max((s[1] for s in window_samples), default=None)
rss_peak_bytes = rss_peak_kib * 1024 if rss_peak_kib is not None else None
rss_delta_bytes = (
    rss_peak_bytes - rss_start_bytes
    if (rss_peak_bytes is not None and rss_start_bytes is not None)
    else None
)

native_resource = {
    "note": "Native macOS ARM64 only — process_resident_memory_bytes is not exposed by the Prometheus Java client's StandardExports on macOS (Linux-only /proc-based metric), so RSS here comes from a separate macOS ps sampler instead. CPU/JVM threads/heap/GC remain Prometheus-authoritative (see the top-level result fields), unaffected by this. Same sampler/interval used for every native P-E and VT-Limited run.",
    "rss_source": "macOS ps -o rss= (Gateway JVM PID only)",
    "sampling_interval_seconds": int(interval),
    "raw_samples_file": "native-rss-samples.csv",
    "sample_count_total": len(samples),
    "sample_count_in_measurement_window": len(window_samples),
    "rss_measurement_start_bytes": rss_start_bytes,
    "rss_measurement_peak_bytes": rss_peak_bytes,
    "rss_measurement_delta_bytes": rss_delta_bytes,
}

with open(result_path) as f:
    result = json.load(f)
result["native_resource"] = native_resource
with open(result_path, "w") as f:
    json.dump(result, f, indent=2)

print(json.dumps(native_resource, indent=2))
PYEOF

echo "=== [${RUN_ID}] $(date -u +%Y-%m-%dT%H:%M:%SZ) done ==="
