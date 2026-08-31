#!/usr/bin/env python3
"""Phase 4 Open Unit 8.1 collector — DIAGNOSTIC ONLY, no canonical classification.

Reduces one run directory (direct-mock-r256/ or m2-r256-two-leg/) into result.json:
  * k6 client counters (scenario 06) + built-in http/iteration/vus metrics
  * two-leg socket timeline -> per-leg state peaks, unique-source-port peaks, UNION/INTERSECT peaks
  * raw socket rows -> sanity totals + per-second first/last for correlation
  * mock metrics, gateway metrics, prometheus range series peaks (m2 only)
  * gateway.log BindException count + timestamps
  * for direct-mock: evaluates the section-10 PASS gate and prints GATE=PASS|FAIL

Usage: collect_phase4_unit81.py <run_dir>
"""
import csv
import json
import re
import sys
from pathlib import Path


def _num(x, d=0.0):
    try:
        return float(x)
    except Exception:
        return d


def load_k6_summary(p):
    if not p.exists():
        return {}
    d = json.loads(p.read_text())
    m = d.get("metrics", {})

    def c(name):
        return _num(m.get(name, {}).get("count", 0))

    def v(name, key):
        return _num(m.get(name, {}).get(key, -1), -1)

    return {
        "iterations": c("iterations"),
        "http_reqs": c("http_reqs"),
        "http_req_duration_min_ms": v("http_req_duration", "min"),
        "http_req_duration_avg_ms": v("http_req_duration", "avg"),
        "http_req_duration_p95_ms": v("http_req_duration", "p(95)"),
        "vus_max": v("vus", "max"),
        "vus_max_configured": v("vus_max", "max"),
        "measurement_iterations_started_total": c("measurement_iterations_started_total"),
        "client_iterations_started_total": c("client_iterations_started_total"),
        "client_sse_open_attempt_total": c("client_sse_open_attempt_total"),
        "client_sse_open_success_total": c("client_sse_open_success_total"),
        "client_first_event_total": c("client_first_event_total"),
        "client_completed_total": c("client_completed_total"),
        "client_failed_mid_stream_total": c("client_failed_mid_stream_total"),
        "client_failed_no_event_total": c("client_failed_no_event_total"),
        "client_zero_event_terminal_total": c("client_zero_event_terminal_total"),
        "client_sse_error_total": c("client_sse_error_total"),
        "client_rejected_total": c("client_rejected_total"),
        "client_events_received_total": c("client_events_received_total"),
        "client_ttfc_completed_p95_s": v("client_ttfc_completed_seconds", "p(95)"),
        "client_completed_stream_duration_p95_s": v("client_completed_stream_duration_seconds", "p(95)"),
        "dropped_iterations": c("dropped_iterations"),
    }


STATE_COLS = ["established", "syn_sent", "time_wait", "close_wait",
              "fin_wait_1", "fin_wait_2", "last_ack", "closing", "other"]


def load_timeline(p):
    if not p.exists():
        return {}
    per_leg = {}
    with p.open() as f:
        for row in csv.DictReader(f):
            leg = row["leg"]
            d = per_leg.setdefault(leg, {c: [] for c in STATE_COLS})
            d.setdefault("unique_src_ports", [])
            d.setdefault("total_rows", [])
            d.setdefault("epoch_ms", [])
            for c in STATE_COLS:
                d[c].append(int(row[c]))
            d["unique_src_ports"].append(int(row["unique_src_ports"]))
            d["total_rows"].append(int(row["total_rows"]))
            d["epoch_ms"].append(int(row["epoch_ms"]))
    out = {}
    for leg, d in per_leg.items():
        peak_i = d["unique_src_ports"].index(max(d["unique_src_ports"])) if d["unique_src_ports"] else -1
        out[leg] = {
            "samples": len(d["unique_src_ports"]),
            "unique_src_ports_peak": max(d["unique_src_ports"]) if d["unique_src_ports"] else 0,
            "unique_src_ports_peak_epoch_ms": d["epoch_ms"][peak_i] if peak_i >= 0 else None,
            "total_rows_peak": max(d["total_rows"]) if d["total_rows"] else 0,
            **{f"{c}_peak": (max(d[c]) if d[c] else 0) for c in STATE_COLS},
        }
    return out


def load_raw_sanity(p):
    if not p.exists():
        return {}
    n = 0
    legs = {}
    states = {}
    with p.open() as f:
        r = csv.DictReader(f)
        for row in r:
            n += 1
            legs[row["leg"]] = legs.get(row["leg"], 0) + 1
            states[row["state"]] = states.get(row["state"], 0) + 1
    return {"raw_row_count": n, "rows_by_leg": legs, "rows_by_state": states}


def load_mock(p):
    if not p.exists():
        return {}
    t = p.read_text()

    def g(metric):
        m = re.search(r"^%s\s+([0-9.eE+-]+)\s*$" % re.escape(metric), t, re.M)
        return _num(m.group(1)) if m else None

    failed = {}
    for m in re.finditer(r'^mockllm_failed_requests_total\{reason="([^"]+)"\}\s+([0-9.eE+-]+)', t, re.M):
        failed[m.group(1)] = _num(m.group(2))
    return {
        "completed": g("mockllm_completed_requests_total"),
        "cancelled": g("mockllm_cancelled_requests_total"),
        "failed": failed,
        "active_final": g("mockllm_active_requests"),
        "waiting_final": g("mockllm_waiting_requests"),
        "current_concurrency_final": g("mockllm_current_concurrency"),
    }


def load_gateway_metrics(p):
    if not p.exists():
        return {}
    t = p.read_text()

    def g(metric):
        m = re.search(r"^%s\s+([0-9.eE+-]+)\s*$" % re.escape(metric), t, re.M)
        return _num(m.group(1)) if m else None

    return {
        "gateway_active_requests_final": g("gateway_active_requests"),
        "virtual_tasks_active_final": g("virtual_tasks_active"),
        "virtual_tasks_started_total_final": g("virtual_tasks_started_total"),
        "tomcat_connections_current_final": g("tomcat_connections_current_connections"),
    }


def load_prom_peak(p):
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
        vals = [float(v[1]) for r in d["data"]["result"] for v in r["values"]]
        if not vals:
            return None
        half = len(vals) // 2 or 1
        return {"n": len(vals), "min": min(vals), "max": max(vals),
                "mean": sum(vals) / len(vals),
                "first_half_mean": sum(vals[:half]) / half,
                "second_half_mean": sum(vals[half:]) / (len(vals) - half or 1)}
    except Exception:
        return None


def load_bindexceptions(p):
    if not p.exists():
        return {"count": 0, "timestamps": [], "full_stack_present": False}
    ts = []
    full_stack = False
    for line in p.read_text().splitlines():
        if "BindException" in line and "Can't assign requested address" in line:
            m = re.match(r"^(\S+)", line)
            ts.append(m.group(1) if m else line[:30])
        if line.strip().startswith("at java.") or line.strip().startswith("at sun."):
            full_stack = True
    return {"count": len(ts), "timestamps": ts, "full_stack_present": full_stack}


def main():
    run_dir = Path(sys.argv[1])
    env = json.loads((run_dir / "environment.json").read_text())
    target = env["target"]

    k6 = load_k6_summary(run_dir / "k6-summary.json")
    tl = load_timeline(run_dir / "two-leg-socket-timeline.csv")
    raw = load_raw_sanity(run_dir / "raw-socket-observations.csv")
    mock = load_mock(run_dir / "mock-metrics-final.txt")
    gw = load_gateway_metrics(run_dir / "gateway-metrics-final.txt")
    binde = load_bindexceptions(run_dir / "gateway.log")
    k6_exit = (run_dir / "k6-exit-code.txt").read_text().strip() if (run_dir / "k6-exit-code.txt").exists() else "?"
    sock_meta = {}
    if (run_dir / "two-leg-socket-meta.json").exists():
        sock_meta = json.loads((run_dir / "two-leg-socket-meta.json").read_text())

    result = {
        "diagnostic_unit": "8.1-m2-r256-two-leg-forensics",
        "canonical": False,
        "target": target,
        "run_label": env.get("run_label"),
        "k6_exit_code": k6_exit,
        "ephemeral_port_range_size": sock_meta.get("ephemeral_port_range_size"),
        "sampler_cpu_pct_of_one_core": sock_meta.get("sampler_cpu_pct_of_one_core"),
        "k6": k6,
        "socket_timeline_peaks": tl,
        "raw_socket_sanity": raw,
        "mock": mock,
        "gateway_metrics_final": gw,
        "bindexception": binde,
    }

    if target == "m2":
        for metric in ["virtual_tasks_active", "gateway_active_requests", "process_cpu_usage",
                       "jvm_threads_live_threads", "tomcat_connections_current_connections",
                       "process_files_open_files", "virtual_tasks_started_total"]:
            result.setdefault("prometheus", {})[metric] = load_prom_peak(run_dir / f"prom-{metric}.json")

    # section-10 PASS gate (direct-mock only)
    if target == "direct-mock":
        started = k6.get("client_iterations_started_total", 0)
        opened = k6.get("client_sse_open_attempt_total", 0)
        succ = k6.get("client_sse_open_success_total", 0)
        zero_ev = k6.get("client_zero_event_terminal_total", 0)
        dropped = k6.get("dropped_iterations", 0)
        mid = k6.get("client_failed_mid_stream_total", 0)
        sse_err = k6.get("client_sse_error_total", 0)
        mock_completed = mock.get("completed") or 0
        syn_peak = tl.get("DIRECT", {}).get("syn_sent_peak", 0)
        # arrival: measurement cohort should be ~ RATE*MEASUREMENT_SEC = 256*120 = 30720
        expected_measurement = env["target_rate"] * env["measurement_sec"]
        meas_started = k6.get("measurement_iterations_started_total", 0)
        arrival_ratio = meas_started / expected_measurement if expected_measurement else 0
        checks = {
            "k6_exit_zero": k6_exit == "0",
            "dropped_eq_0": dropped == 0,
            "arrival_within_5pct": 0.95 <= arrival_ratio <= 1.05,
            "open_attempt_eq_success": opened > 0 and abs(opened - succ) <= max(5, 0.001 * opened),
            "zero_event_terminal_eq_0": zero_ev == 0,
            "mid_stream_eq_0": mid == 0,
            "sse_error_eq_0": sse_err == 0,
            "mock_population_consistent": abs((succ or 0) - mock_completed) <= max(10, 0.002 * (succ or 1)),
            "no_syn_sent_accumulation": syn_peak <= max(50, 0.05 * (succ or 1)),
            "no_bindexception": True,  # direct-mock has no gateway.log; EADDRNOTAVAIL would show as k6 zero-event + syn
        }
        gate_pass = all(checks.values())
        result["direct_mock_gate"] = {
            "checks": checks,
            "arrival_ratio": round(arrival_ratio, 4),
            "expected_measurement_cohort": expected_measurement,
            "measurement_started": meas_started,
            "syn_sent_peak": syn_peak,
            "GATE": "PASS" if gate_pass else "FAIL",
        }

    (run_dir / "result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    if target == "direct-mock":
        print("\nGATE=" + result["direct_mock_gate"]["GATE"])


if __name__ == "__main__":
    main()
