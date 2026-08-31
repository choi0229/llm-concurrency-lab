#!/usr/bin/env python3
"""Phase 4 Open Unit 8.1 — endpoint-aware two-leg TCP socket telemetry sampler.

Read-only. Makes NO sysctl / ulimit / OS-tuning changes of any kind. Runs alongside k6 (samples
until the given k6 PID exits) and, once per second, dumps the kernel TCP table via
`netstat -n -p tcp` and splits it into the connection legs this diagnostic cares about:

  LEG A  (m2 run only) : client ephemeral  -> 127.0.0.1:<gateway_port>   (k6 -> Gateway)
  LEG B  (m2 run only) : Gateway ephemeral -> 127.0.0.1:<mock_port>      (Gateway -> Mock)
  DIRECT (direct-mock) : client ephemeral  -> 127.0.0.1:<mock_port>      (k6 -> Mock)

For every leg a row is kept ONLY if  local_port != leg_port  AND  foreign_port == leg_port  — i.e.
the *connecting* side's socket. The loopback peer row (local_port == leg_port, the accepted server
socket) is explicitly excluded so counts are genuine client-side occupancy, not a client+server sum
(the Unit 7.4 double-count correction, carried forward).

Outputs
  --raw-csv       one row per (sample, leg, socket): epoch_ms,leg,local_addr,local_port,
                  foreign_addr,foreign_port,state   -- full raw rows preserved so any figure can be
                  recomputed later (Unit 8.1 governing instruction section 10 / section 29).
  --timeline-csv  per (sample, leg) aggregate, long format. leg is one of the configured leg names
                  plus synthetic rows UNION and INTERSECT (unique-source-port set algebra across the
                  real legs). Columns: epoch_ms,leg,established,syn_sent,time_wait,close_wait,
                  fin_wait_1,fin_wait_2,last_ack,closing,other,unique_src_ports,total_rows
  --meta-json     host ephemeral range / msl / fd limit / sampler pid / start+end / self rusage
                  (sampler CPU seconds, for the section-5 perturbation check).

Usage:
  sample_two_leg_socket_telemetry.py --k6-pid N --legs A:18102,B:8000 \
      --raw-csv raw.csv --timeline-csv timeline.csv --meta-json meta.json
"""
import argparse
import json
import os
import resource
import signal
import subprocess
import sys
import time

# macOS `netstat -n -p tcp` states we may actually see. Anything else falls into "other".
KNOWN_STATES = [
    "ESTABLISHED", "SYN_SENT", "SYN_RCVD", "TIME_WAIT", "CLOSE_WAIT",
    "FIN_WAIT_1", "FIN_WAIT_2", "LAST_ACK", "CLOSING", "CLOSED", "LISTEN",
]
_STATE_COL = {
    "ESTABLISHED": "established", "SYN_SENT": "syn_sent", "TIME_WAIT": "time_wait",
    "CLOSE_WAIT": "close_wait", "FIN_WAIT_1": "fin_wait_1", "FIN_WAIT_2": "fin_wait_2",
    "LAST_ACK": "last_ack", "CLOSING": "closing",
}
_TIMELINE_COLS = ["established", "syn_sent", "time_wait", "close_wait",
                  "fin_wait_1", "fin_wait_2", "last_ack", "closing", "other"]

_stop = False


def _sigterm(_sig, _frm):
    global _stop
    _stop = True


def sysctl(name):
    try:
        return subprocess.check_output(["sysctl", "-n", name], text=True).strip()
    except Exception:
        return "unknown"


def split_hostport(s):
    """'127.0.0.1.52341' -> ('127.0.0.1', '52341'); '*.*' -> ('*','*')."""
    i = s.rfind(".")
    if i < 0:
        return s, ""
    return s[:i], s[i + 1:]


def sample_once(leg_ports):
    """Return (rows_by_leg, epoch_ms). rows_by_leg[leg] = list of (laddr,lport,faddr,fport,state)."""
    epoch_ms = int(time.time() * 1000)
    try:
        out = subprocess.check_output(["netstat", "-n", "-p", "tcp"], text=True,
                                      stderr=subprocess.DEVNULL)
    except Exception:
        return {leg: [] for leg in leg_ports}, epoch_ms
    rows_by_leg = {leg: [] for leg in leg_ports}
    for line in out.splitlines():
        if not (line.startswith("tcp4") or line.startswith("tcp6")):
            continue
        parts = line.split()
        if len(parts) < 6:
            continue
        laddr, lport = split_hostport(parts[3])
        faddr, fport = split_hostport(parts[4])
        state = parts[5]
        for leg, port in leg_ports.items():
            if fport == port and lport != port:
                rows_by_leg[leg].append((laddr, lport, faddr, fport, state))
    return rows_by_leg, epoch_ms


def aggregate(rows):
    agg = {c: 0 for c in _TIMELINE_COLS}
    ports = set()
    for (_laddr, lport, _faddr, _fport, state) in rows:
        col = _STATE_COL.get(state, "other")
        agg[col] += 1
        ports.add(lport)
    return agg, ports


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k6-pid", type=int, required=True)
    ap.add_argument("--legs", required=True, help="comma list name:port, e.g. A:18102,B:8000")
    ap.add_argument("--raw-csv", required=True)
    ap.add_argument("--timeline-csv", required=True)
    ap.add_argument("--meta-json", required=True)
    ap.add_argument("--interval", type=float, default=1.0)
    args = ap.parse_args()

    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)

    leg_ports = {}
    for tok in args.legs.split(","):
        name, port = tok.split(":")
        leg_ports[name.strip()] = port.strip()
    leg_names = list(leg_ports)

    first = int(sysctl("net.inet.ip.portrange.first") or 0) if sysctl(
        "net.inet.ip.portrange.first").isdigit() else "unknown"
    last = sysctl("net.inet.ip.portrange.last")
    last_i = int(last) if last.isdigit() else "unknown"
    range_size = (last_i - first + 1) if isinstance(first, int) and isinstance(last_i, int) else "unknown"

    start_ms = int(time.time() * 1000)
    meta = {
        "diagnostic_unit": "8.1-m2-r256-two-leg-forensics",
        "legs": leg_ports,
        "ephemeral_port_range_first": first,
        "ephemeral_port_range_last": last_i,
        "ephemeral_port_range_size": range_size,
        "net_inet_tcp_msl_ms": sysctl("net.inet.tcp.msl"),
        "kern_maxfilesperproc": sysctl("kern.maxfilesperproc"),
        "kern_ipc_somaxconn": sysctl("kern.ipc.somaxconn"),
        "fd_soft_limit_at_sampler_start": (lambda: str(resource.getrlimit(resource.RLIMIT_NOFILE)[0]))(),
        "sampler_pid": os.getpid(),
        "k6_pid": args.k6_pid,
        "sample_interval_s": args.interval,
        "start_epoch_ms": start_ms,
        "note": "read-only observation; no sysctl/ulimit/OS-tuning changes made",
    }
    with open(args.meta_json, "w") as f:
        json.dump(meta, f, indent=2)

    raw_f = open(args.raw_csv, "w", buffering=1)
    tl_f = open(args.timeline_csv, "w", buffering=1)
    raw_f.write("epoch_ms,leg,local_addr,local_port,foreign_addr,foreign_port,state\n")
    tl_f.write("epoch_ms,leg," + ",".join(_TIMELINE_COLS) + ",unique_src_ports,total_rows\n")

    samples = 0
    while not _stop:
        try:
            os.kill(args.k6_pid, 0)
        except OSError:
            break
        rows_by_leg, epoch_ms = sample_once(leg_ports)
        per_leg_ports = {}
        for leg in leg_names:
            rows = rows_by_leg[leg]
            for (laddr, lport, faddr, fport, state) in rows:
                raw_f.write(f"{epoch_ms},{leg},{laddr},{lport},{faddr},{fport},{state}\n")
            agg, ports = aggregate(rows)
            per_leg_ports[leg] = ports
            tl_f.write(f"{epoch_ms},{leg}," + ",".join(str(agg[c]) for c in _TIMELINE_COLS) +
                       f",{len(ports)},{len(rows)}\n")
        if len(leg_names) >= 2:
            union = set()
            for p in per_leg_ports.values():
                union |= p
            inter = set(per_leg_ports[leg_names[0]])
            for leg in leg_names[1:]:
                inter &= per_leg_ports[leg]
            zeros = ",".join("0" for _ in _TIMELINE_COLS)
            tl_f.write(f"{epoch_ms},UNION,{zeros},{len(union)},{len(union)}\n")
            tl_f.write(f"{epoch_ms},INTERSECT,{zeros},{len(inter)},{len(inter)}\n")
        samples += 1
        time.sleep(args.interval)

    ru = resource.getrusage(resource.RUSAGE_SELF)
    end_ms = int(time.time() * 1000)
    elapsed_s = (end_ms - start_ms) / 1000.0
    cpu_s = ru.ru_utime + ru.ru_stime
    meta.update({
        "end_epoch_ms": end_ms,
        "elapsed_s": round(elapsed_s, 3),
        "samples_taken": samples,
        "sampler_cpu_user_s": round(ru.ru_utime, 3),
        "sampler_cpu_sys_s": round(ru.ru_stime, 3),
        "sampler_cpu_total_s": round(cpu_s, 3),
        "sampler_cpu_pct_of_one_core": round(100.0 * cpu_s / elapsed_s, 2) if elapsed_s > 0 else None,
        "sampler_maxrss_kib": ru.ru_maxrss // 1024,
    })
    with open(args.meta_json, "w") as f:
        json.dump(meta, f, indent=2)
    raw_f.close()
    tl_f.close()


if __name__ == "__main__":
    main()
