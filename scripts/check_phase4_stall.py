#!/usr/bin/env python3
"""Phase 4 Closed-model environment-stall checker (Unit 4.5).

Design rationale (docs/test-plan/phase4-closed-harness-protocol.md section 9 amendment): a naive
"any counter plateau > N seconds = stall" rule does NOT work for Phase 4's Closed single-wave
model, because EVERY relevant Gateway/Mock counter/gauge has long, entirely normal plateaus as part
of its expected shape:
  - gateway_active_requests / mockllm_active_requests jump to N near wave-start and STAY at N for
    the whole ~7.8-7.9s stream duration (a wide, expected plateau).
  - gateway_requests_started_total / mockllm's own request-count reach N almost immediately (near
    wave-start) then sit flat for the rest of the wave (another expected plateau).
  - terminal/completed counters sit at 0 for ~7.8-7.9s then jump to N near-simultaneously.
Actual investigation of raw Unit 4 evidence (m1/m2/m3-n20-harness-smoke) found gateway.log carries
almost no lines during the active window BY DESIGN (docs/test-plan/phase4-design.md Unit 2 section
44 "Logging Policy" -- no per-request/per-chunk INFO logging on the hot path), so a Gateway
*log*-gap check would misfire on every single healthy run. Two signals ARE reliable instead:

  1. Gateway scrape-heartbeat gap: the existing 1Hz Prometheus range-query timestamps (already
     collected for any metric, e.g. jvm_threads_live_threads) are themselves a heartbeat -- a gap
     bigger than a few scrape intervals means Prometheus failed to scrape the Gateway's
     /actuator/prometheus endpoint, i.e. the Gateway process was unresponsive to HTTP.
  2. Mock application-log gap: mock.log's timestamped lines ("stream requested"/"completed"/error,
     docs/decisions/mock-llm source) DO fire once per request lifecycle event -- for a closed wave
     with N requests started together, the natural gap between the "requested" burst and the
     "completed" burst is ~7.8-7.9s (the workload's own service time), repeated once per prewarm
     request (sequential) and once for the wave (concurrent). This is the normal, expected shape.

Neither signal alone reliably distinguishes "normal wave shape" from "real stall" for arbitrary
thresholds -- but a gap MUCH larger than the ~7.8-7.9s natural unit (this checker uses 15s, ~2x
margin, well under CHAT_TOTAL_TIMEOUT_MS=60s) on EITHER signal, correlated with the OTHER signal or
with a client-side latency anomaly, is not explainable by normal wave shape.

Multi-signal rule (docs/test-plan/phase4-design.md Unit 4.5 brief section 3):
    stall_detected = (gateway_gap_exceeds AND mock_gap_exceeds)
                      OR (client_duration_anomaly AND (gateway_gap_exceeds OR mock_gap_exceeds))
A single, uncorrelated log-gap signal with no client-visible impact is recorded as advisory
evidence but does NOT flip stall_detected -- avoids false positives from an isolated blip
(e.g. one slow scrape) while still catching a real environment-wide freeze.
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

GATEWAY_GAP_THRESHOLD_S = 5.0     # Prometheus scrapes at 1Hz; a multi-second scrape gap while the
                                   # Gateway is supposed to be running is already anomalous.
MOCK_GAP_THRESHOLD_S = 15.0       # ~2x margin over the workload's own ~7.8-7.9s natural quiet
                                   # period (chunkCount=35, chunkIntervalMs=200, firstChunkDelayMs=1000).
CLIENT_DURATION_ANOMALY_S = 20.0  # ~2.5x the ~7.8-7.9s baseline stream duration.

MOCK_LOG_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})")


def parse_mock_log_gap(mock_log_path, window_start_s, window_end_s):
    """Max gap (seconds) between consecutive TIMESTAMPED mock.log lines within the window.
    Untimestamped uvicorn access-log lines are ignored (see module docstring)."""
    path = Path(mock_log_path)
    if not path.exists():
        return {"available": False, "max_gap_s": None, "line_count": 0}
    timestamps = []
    for line in path.read_text(errors="replace").splitlines():
        m = MOCK_LOG_TS_RE.match(line)
        if not m:
            continue
        try:
            dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S,%f")
        except ValueError:
            continue
        epoch_s = dt.timestamp()
        if window_start_s <= epoch_s <= window_end_s:
            timestamps.append(epoch_s)
    timestamps.sort()
    if len(timestamps) < 2:
        return {"available": True, "max_gap_s": None, "line_count": len(timestamps)}
    max_gap = max(b - a for a, b in zip(timestamps, timestamps[1:]))
    return {"available": True, "max_gap_s": max_gap, "line_count": len(timestamps)}


def parse_prometheus_gap(prom_json_path, window_start_s, window_end_s):
    """Max gap (seconds) between consecutive Prometheus sample timestamps (any metric already
    range-queried for this run) within the window -- a scrape-success heartbeat for the Gateway."""
    path = Path(prom_json_path)
    if not path.exists():
        return {"available": False, "max_gap_s": None, "sample_count": 0}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {"available": False, "max_gap_s": None, "sample_count": 0}
    if data.get("status") != "success":
        return {"available": False, "max_gap_s": None, "sample_count": 0}
    timestamps = set()
    for series in data.get("data", {}).get("result", []):
        for ts, _ in series.get("values", []):
            if window_start_s <= ts <= window_end_s:
                timestamps.add(ts)
    timestamps = sorted(timestamps)
    if len(timestamps) < 2:
        return {"available": True, "max_gap_s": None, "sample_count": len(timestamps)}
    max_gap = max(b - a for a, b in zip(timestamps, timestamps[1:]))
    return {"available": True, "max_gap_s": max_gap, "sample_count": len(timestamps)}


def client_duration_anomaly(k6_summary):
    m = (k6_summary or {}).get("metrics", {}).get("client_stream_duration_seconds", {})
    max_dur = m.get("max")
    return {
        "max_duration_s": max_dur,
        "anomaly": (max_dur is not None and max_dur > CLIENT_DURATION_ANOMALY_S),
    }


def check_stall(run_dir):
    run_dir = Path(run_dir)
    ts = json.loads((run_dir / "timestamps.json").read_text()) if (run_dir / "timestamps.json").exists() else {}
    k6 = json.loads((run_dir / "k6-summary.json").read_text()) if (run_dir / "k6-summary.json").exists() else {}

    window_start_s = ts.get("k6_wall_start_ms", 0) / 1000.0
    window_end_s = ts.get("k6_wall_end_ms", 0) / 1000.0

    mock_gap = parse_mock_log_gap(run_dir / "mock.log", window_start_s, window_end_s)
    # Any already-collected range-query file works as the Gateway scrape heartbeat -- prefer
    # jvm_threads_live_threads (always present per Unit 2).
    gw_gap = parse_prometheus_gap(run_dir / "prom-jvm_threads_live_threads.json", window_start_s, window_end_s)
    client = client_duration_anomaly(k6)

    gw_exceeds = bool(gw_gap.get("max_gap_s") is not None and gw_gap["max_gap_s"] > GATEWAY_GAP_THRESHOLD_S)
    mock_exceeds = bool(mock_gap.get("max_gap_s") is not None and mock_gap["max_gap_s"] > MOCK_GAP_THRESHOLD_S)

    stall_detected = bool((gw_exceeds and mock_exceeds) or (client["anomaly"] and (gw_exceeds or mock_exceeds)))

    evidence = []
    if gw_exceeds:
        evidence.append(f"gateway scrape-heartbeat gap {gw_gap['max_gap_s']:.1f}s > {GATEWAY_GAP_THRESHOLD_S}s threshold")
    if mock_exceeds:
        evidence.append(f"mock log gap {mock_gap['max_gap_s']:.1f}s > {MOCK_GAP_THRESHOLD_S}s threshold")
    if client["anomaly"]:
        evidence.append(f"client stream_duration max {client['max_duration_s']:.1f}s > {CLIENT_DURATION_ANOMALY_S}s threshold")
    if not evidence:
        evidence.append("no anomaly signal observed")

    result = {
        "query_ok": gw_gap["available"] and mock_gap["available"],
        "window_start_s": window_start_s,
        "window_end_s": window_end_s,
        "gateway_scrape_gap_max_s": gw_gap.get("max_gap_s"),
        "gateway_gap_exceeds_threshold": gw_exceeds,
        "mock_log_gap_max_s": mock_gap.get("max_gap_s"),
        "mock_gap_exceeds_threshold": mock_exceeds,
        "client_max_duration_s": client["max_duration_s"],
        "client_duration_anomaly": client["anomaly"],
        "thresholds": {
            "gateway_gap_s": GATEWAY_GAP_THRESHOLD_S,
            "mock_gap_s": MOCK_GAP_THRESHOLD_S,
            "client_duration_s": CLIENT_DURATION_ANOMALY_S,
        },
        "stall_detected": stall_detected,
        "evidence": evidence,
    }
    (run_dir / "stall-check.json").write_text(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: check_phase4_stall.py <run_dir>", file=sys.stderr)
        sys.exit(2)
    print(json.dumps(check_stall(sys.argv[1]), indent=2))
