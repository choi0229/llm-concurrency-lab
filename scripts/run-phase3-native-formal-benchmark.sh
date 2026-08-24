#!/usr/bin/env bash
# Phase 3 Unit 6.5 -- Formal single-run engine (docs/test-plan/phase3-formal-protocol.md).
# NOT invoked standalone for the canonical 27-run matrix except via
# scripts/run-phase3-native-formal-matrix.sh (which encodes the frozen run order) -- but IS
# invoked directly for the Canary (protocol Sec 29), with LABEL=canary.
#
# Usage: scripts/run-phase3-native-formal-benchmark.sh <A|B|C> <r3|r7|r10> <RATE> <REPEAT_N> [LABEL]
#   LABEL defaults to "run<REPEAT_N>"; the Canary passes LABEL=canary explicitly so its output
#   directory (p3a-r3-canary/) is never mistaken for p3a-r3-run1/ in the canonical matrix.
#
# Fresh Gateway JVM + fresh Mock LLM process per run (Sec 5). Continuous single k6 process for
# warm-up->measurement (Sec 11, scripts/phase3-formal-k6.js). Native macOS arm64 only (Sec 6).
set -uo pipefail

CFG="$1"; LOAD="$2"; RATE="$3"; REPEAT_N="$4"; LABEL="${5:-run${REPEAT_N}}"
WARMUP_SEC="${WARMUP_SEC:-120}"
MEASUREMENT_SEC="${MEASUREMENT_SEC:-300}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JAVA8="$REPO_ROOT/.native-runtime/zulu8.96.0.205-ca-jdk8.0.504-macosx_aarch64/Contents/Home"
K6="$REPO_ROOT/.native-runtime/k6"
K6_SCRIPT="$REPO_ROOT/scripts/phase3-formal-k6.js"
MOCK_DIR="$REPO_ROOT/mock-llm-fastapi"
MOCK_PORT=8000
MOCK_URL="http://127.0.0.1:${MOCK_PORT}"

cfg_slug() { case "$1" in A) echo p3a ;; B) echo p3b ;; C) echo p3c ;; esac; }
module_dir() { case "$1" in A) echo "$REPO_ROOT/gateway-mvc-blocking-spring5" ;; B) echo "$REPO_ROOT/gateway-mvc-webclient" ;; C) echo "$REPO_ROOT/gateway-webflux" ;; esac; }
gateway_port() { case "$1" in A) echo 8081 ;; B) echo 8082 ;; C) echo 8083 ;; esac; }

SLUG="$(cfg_slug "$CFG")"
DIR="$(module_dir "$CFG")"
PORT="$(gateway_port "$CFG")"
RUN_ID="${SLUG}-${LOAD}-${LABEL}"
OUT_DIR="$REPO_ROOT/docs/test-results/phase3/unit7-formal/${RUN_ID}"
SCRATCH="${PHASE3_FORMAL_SCRATCH:-/tmp/phase3-formal}/${RUN_ID}"
mkdir -p "$OUT_DIR" "$SCRATCH/prom-snapshots"

log() { echo "[formal][${RUN_ID}] $*"; }
find_jar() { find "$1/build/libs" -maxdepth 1 -name "*.jar" ! -name "*-plain.jar" 2>/dev/null | head -1; }

GATEWAY_PID=""; MOCK_PID=""; SAMPLER_PID=""; RSS_PID=""; CAFFEINATE_PID=""
POSTFLIGHT_FAIL=""; INVALID_REASON=""

cleanup() {
  for pidvar in SAMPLER_PID RSS_PID CAFFEINATE_PID GATEWAY_PID MOCK_PID; do
    pid="${!pidvar}"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null
      for i in $(seq 1 10); do kill -0 "$pid" 2>/dev/null || break; sleep 0.3; done
      kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
    fi
  done
}
trap cleanup EXIT

# ---- Preflight (Sec 5/13-A) ----
PREFLIGHT_FAIL=""
pgrep -fq "Docker Desktop|com.docker.backend" && PREFLIGHT_FAIL="${PREFLIGHT_FAIL} docker_desktop_running"
for p in "$PORT" "$MOCK_PORT"; do
  lsof -i ":${p}" -sTCP:LISTEN >/dev/null 2>&1 && PREFLIGHT_FAIL="${PREFLIGHT_FAIL} port_${p}_in_use"
done
JAR="$(find_jar "$DIR")"
if [ -z "$JAR" ]; then
  log "building jar..."
  ( cd "$DIR" && JAVA_HOME="$JAVA8" ./gradlew --no-daemon bootJar >> "$OUT_DIR/build.log" 2>&1 )
  JAR="$(find_jar "$DIR")"
fi
[ -n "$JAR" ] || PREFLIGHT_FAIL="${PREFLIGHT_FAIL} jar_build_failed"
if [ -n "$PREFLIGHT_FAIL" ]; then
  log "FATAL preflight:${PREFLIGHT_FAIL}"
  echo "{\"valid\": false, \"invalid_reason\": \"preflight:${PREFLIGHT_FAIL}\"}" > "$OUT_DIR/result.json"
  exit 1
fi

caffeinate -dims -w $$ &
CAFFEINATE_PID=$!

PROCESS_START=$(date +%s.%N)

# ---- Fresh Mock LLM ----
pkill -f "uvicorn app.main:app" 2>/dev/null || true
sleep 1
( cd "$MOCK_DIR" && nohup .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$MOCK_PORT" \
    > "$OUT_DIR/mock.log" 2>&1 & )
for _ in $(seq 1 30); do curl -s -o /dev/null "${MOCK_URL}/healthz" && break; sleep 0.5; done
curl -s -o /dev/null "${MOCK_URL}/healthz" || { log "FATAL: mock not healthy"; INVALID_REASON="mock_unhealthy"; }
MOCK_PID=$(pgrep -f "uvicorn app.main:app" | head -1)

# ---- Fresh Gateway JVM (Sec 8 config freeze) ----
pkill -f "$(basename "$JAR")" 2>/dev/null || true
sleep 1
ENV_ARGS=(SERVER_PORT="$PORT" MOCK_LLM_BASE_URL="$MOCK_URL" CHAT_ADMISSION_LIMIT=50 CHAT_TOTAL_TIMEOUT_MS=60000)
IMPL_CONFIG_JSON=""
case "$CFG" in
  A) ENV_ARGS+=(CHAT_BLOCKING_POOL_SIZE=50); IMPL_CONFIG_JSON=', "CHAT_BLOCKING_POOL_SIZE": 50' ;;
  B) ENV_ARGS+=(WEBCLIENT_MAX_CONNECTIONS=50); IMPL_CONFIG_JSON=', "WEBCLIENT_MAX_CONNECTIONS": 50' ;;
  C) ENV_ARGS+=(WEBCLIENT_MAX_CONNECTIONS=50); IMPL_CONFIG_JSON=', "WEBCLIENT_MAX_CONNECTIONS": 50' ;;
esac
( cd "$DIR" && env "${ENV_ARGS[@]}" nohup "$JAVA8/bin/java" -jar "$JAR" > "$OUT_DIR/gateway.log" 2>&1 & )
for _ in $(seq 1 40); do curl -s -o /dev/null "http://127.0.0.1:${PORT}/healthz" && break; sleep 0.5; done
curl -s -o /dev/null "http://127.0.0.1:${PORT}/healthz" || { log "FATAL: gateway not healthy"; INVALID_REASON="gateway_unhealthy"; }

# GATEWAY_PID via pgrep AFTER the health check confirms the real process is up (Sec 22 --
# the Unit 6 Screening fix: $! after an env/nohup chain does not reliably match the actual java
# process; verified again explicitly by the Canary, Sec 29).
GATEWAY_PID=$(pgrep -f "$(basename "$JAR")" | head -1)
if [ -z "$GATEWAY_PID" ]; then
  log "FATAL: could not resolve GATEWAY_PID via pgrep"
  echo "{\"valid\": false, \"invalid_reason\": \"gateway_pid_unresolved\"}" > "$OUT_DIR/result.json"
  exit 1
fi
GATEWAY_CMDLINE=$(ps -o command= -p "$GATEWAY_PID")
echo "$GATEWAY_PID" > "$OUT_DIR/gateway.pid"
echo "$GATEWAY_CMDLINE" > "$OUT_DIR/gateway.cmdline.txt"

# ---- Prometheus 1Hz sampler (continuous, full run -- serves as this native harness's
# stand-in for a queryable TSDB; window-filtered aggregation happens in the collector against
# this CSV + the raw numbered snapshots) ----
PROM_CSV="$OUT_DIR/prometheus-samples.csv"
echo "ts,jvm_threads_live,process_cpu_seconds_total,process_cpu_usage,heap_used,gc_pause_count,gc_pause_sum,admission_active,active_streams,upstream_active,servlet_write_active,servlet_write_queue,servlet_write_buffered,servlet_write_rejected,write_overflow,blocking_executor_active,blocking_executor_rejected,conn_pending" > "$PROM_CSV"
SNAP_IDX_FILE="$SCRATCH/snap-index.csv"
echo "idx,ts" > "$SNAP_IDX_FILE"
( idx=0
  while kill -0 "$GATEWAY_PID" 2>/dev/null; do
    ts=$(date +%s.%N)
    m=$(curl -s -m 1 "http://127.0.0.1:${PORT}/actuator/prometheus" 2>/dev/null)
    printf '%05d' "$idx" > /dev/null
    snapfile="$SCRATCH/prom-snapshots/$(printf '%05d' "$idx").txt"
    echo "$m" > "$snapfile"
    echo "$idx,$ts" >> "$SNAP_IDX_FILE"
    g() { echo "$m" | awk -v k="$1" '$0 ~ "^"k" " {print $NF; exit} $0 ~ "^"k"\\{" {print $NF; exit}'; }
    # jvm_memory_used_bytes/jvm_gc_pause_seconds_* are broken down by area/pool/cause label --
    # a plain first-match grab (like g() above) would silently pick one arbitrary pool/cause
    # instead of the total. Sum matching lines instead (heap-area only for memory).
    gsum() { echo "$m" | awk -v pat="$1" '$0 ~ pat {s+=$NF} END{print s+0}'; }
    heap_used=$(gsum '^jvm_memory_used_bytes\{area="heap"')
    gc_count=$(gsum '^jvm_gc_pause_seconds_count\{')
    gc_sum=$(gsum '^jvm_gc_pause_seconds_sum\{')
    echo "$ts,$(g jvm_threads_live_threads),$(g process_cpu_seconds_total),$(g process_cpu_usage),${heap_used},${gc_count},${gc_sum},$(g gateway_admission_active),$(g gateway_active_streams),$(g gateway_upstream_active),$(g servlet_write_executor_active),$(g servlet_write_executor_queue_depth),$(g servlet_write_stream_buffered_frames),$(g servlet_write_executor_rejected_total),$(g gateway_write_overflow_total),$(g outbound_blocking_executor_active),$(g outbound_blocking_executor_rejected_total),$(g reactor_netty_connection_provider_pending_connections)" >> "$PROM_CSV"
    idx=$((idx + 1))
    sleep 1
  done ) &
SAMPLER_PID=$!

# ---- RSS sampler (1Hz, native ps -o rss=, Sec 22) ----
RSS_CSV="$OUT_DIR/rss-samples.csv"
echo "ts,rss_kib" > "$RSS_CSV"
( while kill -0 "$GATEWAY_PID" 2>/dev/null; do
    ts=$(date +%s.%N)
    rss=$(ps -o rss= -p "$GATEWAY_PID" 2>/dev/null | tr -d ' ')
    [ -n "$rss" ] && echo "$ts,$rss" >> "$RSS_CSV"
    sleep 1
  done ) &
RSS_PID=$!

WARMUP_START=$(date +%s.%N)
log "continuous k6 start: rate=$RATE warmup=${WARMUP_SEC}s measurement=${MEASUREMENT_SEC}s"

VUS_BASE=$((RATE * 20 + 50))
"$K6" run --summary-export "$OUT_DIR/k6-summary.json" \
  --summary-trend-stats "avg,p(50),p(95),p(99),min,max" \
  -e GATEWAY_URL="http://127.0.0.1:${PORT}/chat/stream" \
  -e RATE="$RATE" -e WARMUP_SEC="$WARMUP_SEC" -e MEASUREMENT_SEC="$MEASUREMENT_SEC" \
  -e FIRST_CHUNK_DELAY_MS=1000 -e CHUNK_INTERVAL_MS=200 -e CHUNK_COUNT=35 -e CHUNK_SIZE_BYTES=64 \
  -e PRE_ALLOCATED_VUS="$VUS_BASE" -e MAX_VUS=$((VUS_BASE * 4)) \
  "$K6_SCRIPT" > "$OUT_DIR/k6-output.txt" 2>&1
K6_EXIT=$?
log "k6 exit=$K6_EXIT"

# ---- measurement_start/measurement_end: single source of truth read back from k6 (Sec 25) ----
MEASUREMENT_START=$(python3 -c "
import json, sys
try:
    with open('$OUT_DIR/k6-summary.json') as f:
        d = json.load(f)
    print(d['metrics']['measurement_start_epoch_s']['value'])
except Exception as e:
    print('', file=sys.stderr); sys.exit(1)
" 2>/dev/null)
if [ -z "$MEASUREMENT_START" ]; then
  log "FATAL: could not read measurement_start_epoch_s from k6-summary.json"
  INVALID_REASON="${INVALID_REASON:+$INVALID_REASON,}measurement_start_unreadable"
  MEASUREMENT_START=0
fi
MEASUREMENT_END=$(python3 -c "print(${MEASUREMENT_START} + ${MEASUREMENT_SEC})" 2>/dev/null || echo 0)

# ---- Drain (Sec 16): poll gauges to 0, hard cap 70s ----
log "confirming drain..."
DRAIN_OK=""
for i in $(seq 1 70); do
  m=$(curl -s -m 2 "http://127.0.0.1:${PORT}/actuator/prometheus" 2>/dev/null)
  a=$(echo "$m" | awk '/^gateway_admission_active /{print $2}')
  s=$(echo "$m" | awk '/^gateway_active_streams /{print $2}')
  u=$(echo "$m" | awk '/^gateway_upstream_active /{print $2}')
  if [ "$a" = "0.0" ] && [ "$s" = "0.0" ] && [ "$u" = "0.0" ]; then DRAIN_OK=1; break; fi
  sleep 1
done
DRAIN_END=$(date +%s.%N)
[ -n "$DRAIN_OK" ] || { POSTFLIGHT_FAIL="${POSTFLIGHT_FAIL} drain_cap_reached"; INVALID_REASON="${INVALID_REASON:+$INVALID_REASON,}postflight_not_clean"; }

curl -s "http://127.0.0.1:${PORT}/actuator/prometheus" > "$OUT_DIR/prometheus-postflight.txt"
POST=$(cat "$OUT_DIR/prometheus-postflight.txt")
check_zero() { v=$(echo "$POST" | awk -v k="$1" '$0 ~ "^"k" " {print $NF; exit}'); [ "$v" = "0.0" ] || [ -z "$v" ] || POSTFLIGHT_FAIL="${POSTFLIGHT_FAIL} $1=$v"; }
check_zero gateway_admission_active
check_zero gateway_active_streams
check_zero gateway_upstream_active
case "$CFG" in
  A) check_zero outbound_blocking_executor_active ;;
esac
case "$CFG" in
  A|B) check_zero servlet_write_executor_active; check_zero servlet_write_executor_queue_depth; check_zero servlet_write_stream_buffered_frames ;;
esac
case "$CFG" in
  B|C) check_zero reactor_netty_connection_provider_pending_connections ;;
esac
[ -z "$POSTFLIGHT_FAIL" ] || INVALID_REASON="${INVALID_REASON:+$INVALID_REASON,}postflight_not_clean:${POSTFLIGHT_FAIL}"

PROCESS_STOP=$(date +%s.%N)

# ---- select closest snapshot to measurement_start/measurement_end from the raw snapshot dir ----
python3 - "$SNAP_IDX_FILE" "$SCRATCH/prom-snapshots" "$MEASUREMENT_START" "$MEASUREMENT_END" \
  "$OUT_DIR/prometheus-measurement-start.txt" "$OUT_DIR/prometheus-measurement-end.txt" <<'PYEOF'
import sys, csv
idx_file, snap_dir, m_start, m_end, out_start, out_end = sys.argv[1:7]
m_start, m_end = float(m_start), float(m_end)
rows = []
with open(idx_file) as f:
    r = csv.DictReader(f)
    for row in r:
        rows.append((int(row['idx']), float(row['ts'])))
def closest(target):
    if not rows:
        return None
    return min(rows, key=lambda x: abs(x[1] - target))[0]
si = closest(m_start)
ei = closest(m_end)
import shutil
if si is not None:
    shutil.copy(f"{snap_dir}/{si:05d}.txt", out_start)
if ei is not None:
    shutil.copy(f"{snap_dir}/{ei:05d}.txt", out_end)
PYEOF

# ---- counter-plateau-check.json (Sec 15, 20s log-gap threshold, reused from Phase 2) ----
python3 - "$OUT_DIR/gateway.log" "$OUT_DIR/mock.log" "$MEASUREMENT_START" "$MEASUREMENT_END" "$OUT_DIR/counter-plateau-check.json" <<'PYEOF'
import sys, json, re, datetime
gw_log, ml_log, meas_start, meas_end, out_path = sys.argv[1:6]

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
result = {"gateway_log_gaps_over_20s": gw_gaps, "mock_log_gaps_over_20s": ml_gaps,
          "note": "A gap does not by itself invalidate the run -- check overlap with [measurement_start, measurement_end] before judging impact (docs/test-plan/phase3-formal-protocol.md Sec 15)."}
with open(out_path, "w") as f:
    json.dump(result, f, indent=2)
if gw_gaps or ml_gaps:
    print(f"[plateau_check] WARNING: {len(gw_gaps)} gateway gap(s), {len(ml_gaps)} mock gap(s) >20s")
else:
    print("[plateau_check] no gaps >20s detected")
PYEOF

# ---- timestamps.json / environment.json ----
JDK_SHA=$(shasum -a 256 "$JAVA8/bin/java" | awk '{print $1}')
OS_PRODUCT=$(sw_vers -productName); OS_VERSION=$(sw_vers -productVersion); OS_BUILD=$(sw_vers -buildVersion)
NPROC=$(sysctl -n hw.ncpu)
POWER=$(pmset -g batt | head -1 | grep -q "AC Power" && echo AC || echo Battery)

cat > "$OUT_DIR/timestamps.json" <<EOF
{
  "process_start": ${PROCESS_START},
  "warmup_start": ${WARMUP_START},
  "measurement_start": ${MEASUREMENT_START},
  "measurement_end": ${MEASUREMENT_END},
  "drain_end": ${DRAIN_END},
  "process_stop": ${PROCESS_STOP}
}
EOF

cat > "$OUT_DIR/environment.json" <<EOF
{
  "run_id": "${RUN_ID}", "config": "${CFG}", "load": "${LOAD}", "repeat": ${REPEAT_N}, "rate": ${RATE},
  "jdk": {"vendor": "Zulu", "version": "1.8.0_504", "build": "1.8.0_504-b01", "sha256_java_binary": "${JDK_SHA}"},
  "os": {"product": "${OS_PRODUCT}", "version": "${OS_VERSION}", "build": "${OS_BUILD}", "arch": "arm64"},
  "available_processors": ${NPROC}, "power_source": "${POWER}",
  "spring_boot": "2.7.18", "spring_framework": "5.3.31", "reactor_core": "3.4.34",
  "reactor_netty": "1.0.39", "micrometer": "1.9.17",
  "tomcat": $( [ "$CFG" = "C" ] && echo null || echo '"9.0.83"' ),
  "k6_version": "v1.8.0", "xk6_sse_version": "v0.1.11",
  "gateway_config": {"CHAT_ADMISSION_LIMIT": 50, "CHAT_TOTAL_TIMEOUT_MS": 60000, "MOCK_LLM_BASE_URL": "${MOCK_URL}"${IMPL_CONFIG_JSON}},
  "blockhound": false, "docker": false, "slow_client": false
}
EOF

# ---- collector ----
python3 "$REPO_ROOT/scripts/collect_phase3_formal_result.py" "$OUT_DIR" "$CFG" "$INVALID_REASON" "$POSTFLIGHT_FAIL" \
  | tee "$OUT_DIR/result.json" >/dev/null

log "done -> $OUT_DIR (invalid_reason='${INVALID_REASON}')"
