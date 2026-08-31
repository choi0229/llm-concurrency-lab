#!/usr/bin/env bash
# Phase 4 Open Unit 8.1 -- low-frequency, read-only process-ownership spot-check.
#
# Runs alongside k6 (until the k6 PID exits), sampling at a DELIBERATELY LOW rate (default 5s) so it
# adds negligible load. Purpose (Unit 8.1 governing instruction section 4/section 11):
#   * confirm the k6 process actually OWNS the LEG A client sockets (foreign :GATEWAY_PORT)
#   * confirm the Gateway JVM actually OWNS the LEG B outbound sockets (foreign :MOCK_PORT)
# NOT a TIME_WAIT census -- TIME_WAIT sockets lose process ownership, so lsof under-counts them by
# design; that is expected and is why the netstat sampler, not this, is the authority on state
# counts. This is an ownership spot-check only.
#
# Usage: sample_lsof_ownership.sh <k6_pid> <gateway_pid_or_-> <gateway_port> <mock_port> <out_txt> [interval_s]
set -uo pipefail

K6_PID="$1"
GW_PID="$2"          # literal "-" when there is no Gateway (direct-mock run)
GW_PORT="$3"
MOCK_PORT="$4"
OUT="$5"
INTERVAL="${6:-5}"

{
  echo "# Phase 4 Open Unit 8.1 lsof ownership spot-check"
  echo "# k6_pid=$K6_PID gateway_pid=$GW_PID gateway_port=$GW_PORT mock_port=$MOCK_PORT interval_s=$INTERVAL"
  echo "# columns per sample: epoch_s | role | pid | tcp_conn_count | by_state"
  echo "# raw lsof blocks preserved below each summary line"
} > "$OUT"

summarize() {
  # $1 = role label, $2 = pid, $3 = foreign port to filter on
  local role="$1" pid="$2" fport="$3" raw states cnt
  if [ "$pid" = "-" ] || ! kill -0 "$pid" 2>/dev/null; then
    echo "$(date +%s) | $role | $pid | NA | process-not-present" >> "$OUT"
    return
  fi
  raw=$(lsof -nP -a -p "$pid" -iTCP 2>/dev/null)
  # keep only rows whose peer is ->127.0.0.1:<fport>
  local filtered
  filtered=$(echo "$raw" | awk -v fp=":${fport}" '$0 ~ ("->127.0.0.1" fp) || $0 ~ ("->\\[::1\\]" fp)')
  cnt=$(echo "$filtered" | grep -c . )
  states=$(echo "$filtered" | grep -oE '\((ESTABLISHED|SYN_SENT|CLOSE_WAIT|FIN_WAIT_1|FIN_WAIT_2|TIME_WAIT|LAST_ACK|CLOSING)\)' | sort | uniq -c | tr '\n' ' ')
  echo "$(date +%s) | $role | $pid | ${cnt:-0} | ${states:-none}" >> "$OUT"
  {
    echo "--- $(date +%s) $role pid=$pid peer=127.0.0.1:$fport (first 25 matching rows) ---"
    echo "$filtered" | head -25
    echo
  } >> "$OUT"
}

while kill -0 "$K6_PID" 2>/dev/null; do
  summarize "k6->GW(legA)"  "$K6_PID" "$GW_PORT"
  summarize "k6->MOCK"      "$K6_PID" "$MOCK_PORT"
  if [ "$GW_PID" != "-" ]; then
    summarize "GW->MOCK(legB)" "$GW_PID" "$MOCK_PORT"
  fi
  sleep "$INTERVAL"
done
