#!/usr/bin/env bash
# Unit 5.5 D2 helper: run one slow-client k6 scenario against a running gateway, polling
# /actuator/prometheus concurrently, saving snapshots + k6 output under the given evidence dir.
set -uo pipefail

PORT="$1"; DEST="$2"; SLEEP_S="$3"; CHUNK_COUNT="$4"; CHUNK_SIZE="$5"
K6="/Users/user/Documents/GitHub/llm-concurrency-lab/.native-runtime/k6"
SCRIPT="/Users/user/Documents/GitHub/llm-concurrency-lab/scripts/diagnostic-tools/slow-client-k6.js"

mkdir -p "$DEST/snapshots"
rm -f "$DEST/snapshots"/*.txt

FLAG="$(mktemp)"
(
  i=0
  while [ -f "$FLAG" ]; do
    curl -s "http://127.0.0.1:${PORT}/actuator/prometheus" > "$DEST/snapshots/$(printf '%04d' "$i").txt"
    i=$((i + 1))
    sleep 0.3
  done
) &
POLLER_PID=$!
sleep 0.5

"$K6" run \
  -e TARGET_URL="http://127.0.0.1:${PORT}/chat/stream" \
  -e SLEEP_PER_EVENT_SECONDS="$SLEEP_S" -e CHUNK_COUNT="$CHUNK_COUNT" -e CHUNK_SIZE_BYTES="$CHUNK_SIZE" \
  "$SCRIPT" > "$DEST/k6-output.txt" 2>&1

rm -f "$FLAG"
sleep 1
kill "$POLLER_PID" 2>/dev/null
wait "$POLLER_PID" 2>/dev/null
echo "snapshots: $(ls "$DEST/snapshots" | wc -l)"
tail -20 "$DEST/k6-output.txt"
