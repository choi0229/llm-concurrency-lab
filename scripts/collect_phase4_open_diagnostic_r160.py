#!/usr/bin/env python3
"""Phase 4 Open Unit 7.2 -- R=160 Transport/Front-door Isolation DIAGNOSTIC collector.

NOT the canonical Open collector (scripts/collect_phase4_open_result.py, untouched). Produces a
diagnostic-only summary from a run of scripts/run-phase4-open-diagnostic-r160.sh: population
accounting across whatever stages exist for the given target (direct-mock has fewer stages than m2,
since there's no Gateway/Tomcat/ChatController layer at all), socket telemetry peaks, and a parse of
the scenario's diagnostic console.log lines (diag zero_event_terminal / diag sse_error). Never writes
into unit7-open-screening/ and never computes a GREEN/AMBER/RED color -- this is not a Screening
point.

Usage: collect_phase4_open_diagnostic_r160.py <run_dir>
"""
import json
import re
import sys
from pathlib import Path


def metric_line(text, name):
    m = re.search(rf"^{re.escape(name)}\s+([0-9eE.+-]+)$", text, re.MULTILINE)
    return float(m.group(1)) if m else None


def metric_line_labeled(text, name):
    """First matching series for a metric name that carries labels, e.g. name{label="x"} value."""
    m = re.search(rf'^{re.escape(name)}\{{[^}}]*\}}\s+([0-9eE.+-]+)$', text, re.MULTILINE)
    return float(m.group(1)) if m else None


def sum_metric_all_labels(text, name):
    total = 0.0
    found = False
    for m in re.finditer(rf'^{re.escape(name)}\{{[^}}]*\}}\s+([0-9eE.+-]+)$', text, re.MULTILINE):
        total += float(m.group(1))
        found = True
    return total if found else None


def parse_diag_log(k6_stdout_path):
    zero_event_lines = []
    sse_error_lines = []
    if not k6_stdout_path.exists():
        return zero_event_lines, sse_error_lines
    text = k6_stdout_path.read_text(errors="replace")
    for line in text.splitlines():
        if "diag zero_event_terminal" in line:
            zero_event_lines.append(line)
        elif "diag sse_error" in line:
            sse_error_lines.append(line)
    return zero_event_lines, sse_error_lines


def summarize_socket_csv(csv_path):
    if not csv_path.exists():
        return None
    rows = csv_path.read_text().strip().splitlines()
    if len(rows) < 2:
        return {"sample_count": 0}
    header = rows[0].split(",")
    idx = {name: i for i, name in enumerate(header)}
    # Unit 7.4/7.5: sampler is now endpoint-aware -- established/syn_sent/time_wait/close_wait are
    # CLIENT-side only (local port != target, foreign port == target); server_established/
    # server_time_wait (if present -- older pre-Unit-7.5 CSVs won't have these columns) are the
    # target process's own accepted-socket counts, kept separate, never summed with the client ones.
    established, syn_sent, time_wait, close_wait = [], [], [], []
    server_established, server_time_wait = [], []
    has_server_cols = "server_established" in idx and "server_time_wait" in idx
    for row in rows[1:]:
        parts = row.split(",")
        if len(parts) != len(header):
            continue
        established.append(int(parts[idx["established"]]))
        syn_sent.append(int(parts[idx["syn_sent"]]))
        time_wait.append(int(parts[idx["time_wait"]]))
        close_wait.append(int(parts[idx["close_wait"]]))
        if has_server_cols:
            server_established.append(int(parts[idx["server_established"]]))
            server_time_wait.append(int(parts[idx["server_time_wait"]]))
    def stats(xs):
        return {"peak": max(xs) if xs else None, "mean": (sum(xs) / len(xs)) if xs else None}
    result = {
        "sample_count": len(established),
        "established": stats(established),
        "syn_sent": stats(syn_sent),
        "time_wait": stats(time_wait),
        "close_wait": stats(close_wait),
        "endpoint_aware": has_server_cols,
    }
    if has_server_cols:
        result["server_established"] = stats(server_established)
        result["server_time_wait"] = stats(server_time_wait)
    return result


def main():
    run_dir = Path(sys.argv[1])
    env = json.loads((run_dir / "environment.json").read_text())
    target = env["target"]
    k6_summary = json.loads((run_dir / "k6-summary.json").read_text()) if (run_dir / "k6-summary.json").exists() else {}
    metrics = k6_summary.get("metrics", {})

    def m(name, field="count"):
        v = metrics.get(name, {})
        return v.get(field)

    client_cohort = {
        "iterations_total": m("iterations"),
        "measurement_iterations_started": m("measurement_iterations_started_total"),
        "client_iterations_started_total": m("client_iterations_started_total"),
        "client_sse_open_attempt_total": m("client_sse_open_attempt_total"),
        "client_sse_open_success_total": m("client_sse_open_success_total"),
        "client_first_event_total": m("client_first_event_total"),
        "client_sse_error_total": m("client_sse_error_total"),
        "client_completed_total": m("client_completed_total"),
        "client_rejected_total": m("client_rejected_total"),
        "client_failed_mid_stream_total": m("client_failed_mid_stream_total"),
        "client_failed_no_event_total": m("client_failed_no_event_total"),
        "client_zero_event_terminal_total": m("client_zero_event_terminal_total"),
        "dropped_iterations": (m("iterations") - m("http_reqs")) if (m("iterations") is not None and m("http_reqs") is not None) else None,
        "http_reqs": m("http_reqs"),
        "vus_peak": metrics.get("vus", {}).get("max"),
    }

    mock_text = (run_dir / "mock-metrics-final.txt").read_text() if (run_dir / "mock-metrics-final.txt").exists() else ""
    mock = {
        "received_first_chunk_count": metric_line(mock_text, "mockllm_first_chunk_latency_seconds_count"),
        "completed_total": metric_line(mock_text, "mockllm_completed_requests_total"),
        "cancelled_total": metric_line(mock_text, "mockllm_cancelled_requests_total"),
        "active_final": metric_line(mock_text, "mockllm_active_requests"),
        "waiting_final": metric_line(mock_text, "mockllm_waiting_requests"),
    }

    gateway = None
    if target == "m2":
        gw_text = (run_dir / "gateway-metrics-final.txt").read_text() if (run_dir / "gateway-metrics-final.txt").exists() else ""
        gateway = {
            "requests_started_total": metric_line(gw_text, "gateway_requests_started_total"),
            "requests_outcome_total_sum": sum_metric_all_labels(gw_text, "gateway_requests_total"),
            "virtual_tasks_started_total": metric_line(gw_text, "virtual_tasks_started_total"),
            "tomcat_global_request_seconds_count": metric_line_labeled(gw_text, "tomcat_global_request_seconds_count"),
            "tomcat_threads_busy_threads_final": metric_line_labeled(gw_text, "tomcat_threads_busy_threads"),
            "tomcat_threads_current_threads_final": metric_line_labeled(gw_text, "tomcat_threads_current_threads"),
            "tomcat_connections_current_connections_final": metric_line_labeled(gw_text, "tomcat_connections_current_connections"),
            "process_cpu_usage_mean": None,
        }
        cpu_path = run_dir / "prom-process_cpu_usage.json"
        if cpu_path.exists():
            try:
                data = json.loads(cpu_path.read_text())
                vals = [float(v[1]) for v in data["data"]["result"][0]["values"]]
                gateway["process_cpu_usage_mean"] = sum(vals) / len(vals) if vals else None
            except Exception:
                pass
        for metric_name, out_key in [
            ("tomcat_threads_busy_threads", "tomcat_threads_busy_threads_timeseries"),
            ("tomcat_connections_current_connections", "tomcat_connections_current_connections_timeseries"),
            ("virtual_tasks_active", "virtual_tasks_active_timeseries"),
        ]:
            p = run_dir / f"prom-{metric_name}.json"
            if p.exists():
                try:
                    data = json.loads(p.read_text())
                    result = data.get("data", {}).get("result", [])
                    if result:
                        vals = [float(v[1]) for v in result[0]["values"]]
                        gateway[out_key] = {"sample_count": len(vals), "peak": max(vals) if vals else None,
                                             "mean": (sum(vals) / len(vals)) if vals else None}
                    else:
                        gateway[out_key] = {"sample_count": 0, "note": "empty Prometheus result -- see note in SUMMARY about TSDB range"}
                except Exception as e:
                    gateway[out_key] = {"error": str(e)}
        configured_max = env.get("server_tomcat_max_connections")
        peak_info = gateway.get("tomcat_connections_current_connections_timeseries")
        gateway["tomcat_connections_configured_max"] = configured_max
        if configured_max and peak_info and peak_info.get("peak") is not None:
            gateway["tomcat_connections_non_binding"] = peak_info["peak"] < (configured_max * 0.99)
        else:
            gateway["tomcat_connections_non_binding"] = None

    zero_event_lines, sse_error_lines = parse_diag_log(run_dir / "k6-stdout.log")
    socket_summary = summarize_socket_csv(run_dir / "client-socket-telemetry.csv")
    socket_meta = json.loads((run_dir / "client-socket-telemetry-meta.json").read_text()) if (run_dir / "client-socket-telemetry-meta.json").exists() else None

    diagnostic = {
        "canonical": False,
        "unit": "7.2-r160-frontdoor-isolation",
        "target": target,
        "environment": env,
        "client_cohort": client_cohort,
        "mock": mock,
        "gateway": gateway,
        "diag_log": {
            "zero_event_terminal_count": len(zero_event_lines),
            "sse_error_count": len(sse_error_lines),
            "zero_event_terminal_sample": zero_event_lines[:10],
            "sse_error_sample": sse_error_lines[:10],
            "zero_event_terminal_all_unique_shapes": sorted(set(
                re.sub(r"t=\d+", "t=T", re.sub(r"elapsed_ms=\d+", "elapsed_ms=E", l)) for l in zero_event_lines
            ))[:20],
        },
        "socket_telemetry": {"summary": socket_summary, "meta": socket_meta},
    }
    out_path = run_dir / "diagnostic-result.json"
    out_path.write_text(json.dumps(diagnostic, indent=2, default=str))
    print(json.dumps({"target": target, "written": str(out_path)}, indent=2))


if __name__ == "__main__":
    main()
