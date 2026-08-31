#!/usr/bin/env bash
# Phase 4 Open Unit 7.2 -- read-only client-host TCP socket telemetry sampler (macOS native tools
# only, no sysctl/OS tuning changes of any kind). Samples every 1s until the given PID (the k6
# process) exits, counting connection states for the given target port only (Gateway or Mock,
# whichever this diagnostic run is pointed at). Also records the ephemeral port range and the
# current process's file-descriptor limit once, at start, for reference.
#
# Unit 7.4 correction: the original filter (`grep ".${TARGET_PORT}"`) matched the target port in
# EITHER the Local or Foreign address column, which on loopback double-counts every connection (the
# client's own socket -- local=ephemeral, foreign=target -- AND the server's accepted socket --
# local=target, foreign=ephemeral -- are both real, separate kernel sockets, both matched). Confirmed
# via the full Unit 7.2 m2-r160-probe-uncapped time series: established/tomcat_connections_current
# ratio was ~2.0 (145/145 matched samples, median 2.014). This version is endpoint-aware: it counts
# CLIENT-side sockets only (local port != target port AND foreign port == target port), separately
# from SERVER-side accepted sockets (local port == target port), so `established`/`syn_sent`/
# `time_wait`/`close_wait` in the CSV now report genuine client-side occupancy, not a client+server
# sum. No validity threshold depends on this data (Unit 7.3/7.4 instructions) -- recording only.
#
# Usage: sample-client-socket-telemetry.sh <k6_pid> <target_port> <out_csv> <out_meta_json>
set -uo pipefail

K6_PID="$1"
TARGET_PORT="$2"
OUT_CSV="$3"
OUT_META="$4"

PORT_FIRST=$(sysctl -n net.inet.ip.portrange.first 2>/dev/null || echo "unknown")
PORT_LAST=$(sysctl -n net.inet.ip.portrange.last 2>/dev/null || echo "unknown")
FD_SOFT=$(ulimit -n 2>/dev/null || echo "unknown")
MAXFILESPERPROC=$(sysctl -n kern.maxfilesperproc 2>/dev/null || echo "unknown")

cat > "$OUT_META" <<EOF
{
  "ephemeral_port_range_first": "$PORT_FIRST",
  "ephemeral_port_range_last": "$PORT_LAST",
  "ephemeral_port_range_size": $(( PORT_LAST - PORT_FIRST + 1 )),
  "fd_soft_limit_at_sampler_start": "$FD_SOFT",
  "kern_maxfilesperproc": "$MAXFILESPERPROC",
  "target_port": $TARGET_PORT,
  "note": "read-only observation, no sysctl/ulimit changes made"
}
EOF

echo "epoch_s,established,syn_sent,time_wait,close_wait,total_client_matching,server_established,server_time_wait,total_all_tcp" > "$OUT_CSV"

# Endpoint-aware split: awk extracts the port (text after the last '.') from the Local ($4) and
# Foreign ($5) address columns. CLIENT_ONLY = local port != target (i.e., an ephemeral source port)
# AND foreign port == target. SERVER_ONLY = local port == target (the Gateway/Mock's own accepted
# socket, regardless of the client's ephemeral port) -- reported separately for cross-checking
# against the target process's own connection-count metric, never summed into the client figures.
client_filter() {
  awk -v port="$TARGET_PORT" '
    { n = split($4, l, "."); lport = l[n]; n = split($5, f, "."); fport = f[n];
      if (lport != port && fport == port) print }
  '
}
server_filter() {
  awk -v port="$TARGET_PORT" '
    { n = split($4, l, "."); lport = l[n];
      if (lport == port) print }
  '
}

while kill -0 "$K6_PID" 2>/dev/null; do
  epoch=$(date +%s)
  all_tcp=$(netstat -n -p tcp 2>/dev/null)
  client_raw=$(echo "$all_tcp" | client_filter)
  server_raw=$(echo "$all_tcp" | server_filter)
  established=$(echo "$client_raw" | grep -c "ESTABLISHED")
  syn_sent=$(echo "$client_raw" | grep -c "SYN_SENT")
  time_wait=$(echo "$client_raw" | grep -c "TIME_WAIT")
  close_wait=$(echo "$client_raw" | grep -c "CLOSE_WAIT")
  total_client_matching=$(echo "$client_raw" | grep -c .)
  server_established=$(echo "$server_raw" | grep -c "ESTABLISHED")
  server_time_wait=$(echo "$server_raw" | grep -c "TIME_WAIT")
  total_all=$(echo "$all_tcp" | grep -c .)
  echo "${epoch},${established},${syn_sent},${time_wait},${close_wait},${total_client_matching},${server_established},${server_time_wait},${total_all}" >> "$OUT_CSV"
  sleep 1
done
