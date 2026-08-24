#!/usr/bin/env python3
"""Unit 5 common functional harness helper.

Computes Counter deltas (after - before, since Prometheus/Micrometer counters are JVM-lifetime
cumulative values, never reset per scenario -- docs/test-plan/phase3-design.md Unit 5 section 5)
and reports Gauge postflight absolute values, from two raw /actuator/prometheus text dumps.

Usage:
    phase3-prometheus-diff.py counters <before.txt> <after.txt>
        Prints every gateway_requests_total{outcome="..."} series with a nonzero delta, plus a
        few other common counters, as "name{labels} delta=<n>".

    phase3-prometheus-diff.py gauges <after.txt> [name...]
        Prints the absolute (postflight) value of the given gauge metric name(s) (or a fixed
        default set of postflight-invariant gauges if none given).
"""
import re
import sys

LINE_RE = re.compile(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+([0-9.eE+-]+|NaN|\+Inf|-Inf)\s*$')

DEFAULT_POSTFLIGHT_GAUGES = [
    "gateway_active_streams",
    "gateway_admission_active",
    "gateway_upstream_active",
    "servlet_write_executor_active",
    "servlet_write_executor_queue_depth",
    "servlet_write_stream_buffered_frames",
    "outbound_blocking_executor_active",
    "reactor_netty_connection_provider_pending_connections",
]


def parse(path):
    series = {}
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            m = LINE_RE.match(line)
            if not m:
                continue
            name, labels, value = m.group(1), m.group(2) or "", m.group(3)
            try:
                fv = float(value)
            except ValueError:
                continue
            series[name + labels] = fv
    return series


def cmd_counters(before_path, after_path):
    before = parse(before_path)
    after = parse(after_path)
    keys = sorted(set(before.keys()) | set(after.keys()))
    printed = False
    for k in keys:
        if not (k.startswith("gateway_requests_total") or k.startswith("gateway_admission_rejected_total")
                or k.startswith("gateway_timeout_total") or k.startswith("gateway_client_disconnect_total")
                or k.startswith("gateway_upstream_cancel_total") or k.startswith("gateway_requests_started_total")):
            continue
        b = before.get(k, 0.0)
        a = after.get(k, 0.0)
        delta = a - b
        if delta != 0:
            print(f"{k} delta={delta:g}")
            printed = True
    if not printed:
        print("(no nonzero counter deltas found)")


def cmd_gauges(after_path, names):
    after = parse(after_path)
    targets = names if names else DEFAULT_POSTFLIGHT_GAUGES
    for target in targets:
        matched = False
        for k, v in sorted(after.items()):
            base = k.split("{", 1)[0]
            if base == target:
                print(f"{k} = {v:g}")
                matched = True
        if not matched:
            print(f"{target} = (absent)")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    mode = sys.argv[1]
    if mode == "counters":
        cmd_counters(sys.argv[2], sys.argv[3])
    elif mode == "gauges":
        cmd_gauges(sys.argv[2], sys.argv[3:])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
