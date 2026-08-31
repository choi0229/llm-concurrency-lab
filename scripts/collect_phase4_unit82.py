#!/usr/bin/env python3
"""Phase 4 Open Unit 8.2 calibration collector — classifies one M2 rate CONTROL_SAFE / CONTROL_INVALID.

Criteria frozen in docs/decisions/phase4-open-single-host-ephemeral-headroom.md section 4:
  4a transport-cleanliness : dropped=0, EADDRNOTAVAIL=0 (k6+gateway), no dial/connect timeout,
                             no unexplained zero-event, open_attempt~=open_success (<=0.1%),
                             Tomcat connections < 3200, clock ok, postflight OK
  4b ephemeral headroom    : A_union_B unique-source-port PEAK <= 80% of pool (<= 13107 / 16384)
  (4c cliff separation and 4d Direct-Mock cap are judged across rates in the SUMMARY, not here)

Usage: collect_phase4_unit82.py <run_dir>
"""
import csv, gzip, json, re, sys
from pathlib import Path

POOL = 16384
HEADROOM_FRAC = 0.80
HEADROOM_PORTS = int(POOL * HEADROOM_FRAC)  # 13107

STATE_COLS = ["established","syn_sent","time_wait","close_wait","fin_wait_1","fin_wait_2","last_ack","closing","other"]


def _f(x, d=0.0):
    try: return float(x)
    except: return d


def k6_metrics(p):
    d = json.loads(p.read_text()); m = d.get("metrics", {})
    c = lambda n: _f(m.get(n, {}).get("count", 0))
    v = lambda n, k: _f(m.get(n, {}).get(k, -1), -1)
    return dict(
        iterations=c("iterations"), http_reqs=c("http_reqs"),
        http_req_duration_min_ms=v("http_req_duration","min"),
        vus_max=v("vus","max"), vus_max_configured=v("vus_max","max"),
        measurement_started=c("measurement_iterations_started_total"),
        client_iterations_started=c("client_iterations_started_total"),
        client_sse_open_attempt=c("client_sse_open_attempt_total"),
        client_sse_open_success=c("client_sse_open_success_total"),
        client_first_event=c("client_first_event_total"),
        client_completed=c("client_completed_total"),
        client_failed_mid_stream=c("client_failed_mid_stream_total"),
        client_zero_event_terminal=c("client_zero_event_terminal_total"),
        client_sse_error=c("client_sse_error_total"),
        dropped_iterations=c("dropped_iterations"),
        ttfc_completed_p95_s=v("client_ttfc_completed_seconds","p(95)"),
        ttfc_completed_max_s=v("client_ttfc_completed_seconds","max"),
        completed_dur_p95_s=v("client_completed_stream_duration_seconds","p(95)"),
        iteration_duration_p95_ms=v("iteration_duration","p(95)"),
    )


def timeline_peaks(p):
    per = {}
    with p.open() as f:
        for row in csv.DictReader(f):
            leg = row["leg"]; d = per.setdefault(leg, {"uniq": [], "epoch": [], **{c: [] for c in STATE_COLS}})
            d["uniq"].append(int(row["unique_src_ports"])); d["epoch"].append(int(row["epoch_ms"]))
            for c in STATE_COLS: d[c].append(int(row[c]))
    out = {}
    for leg, d in per.items():
        i = d["uniq"].index(max(d["uniq"])) if d["uniq"] else -1
        out[leg] = {"unique_src_ports_peak": max(d["uniq"]) if d["uniq"] else 0,
                    "unique_src_ports_peak_epoch_ms": d["epoch"][i] if i >= 0 else None,
                    **{f"{c}_peak": (max(d[c]) if d[c] else 0) for c in STATE_COLS}}
    return out


def mock_metrics(p):
    t = p.read_text()
    g = lambda k: (lambda m: _f(m.group(1)) if m else None)(re.search(r"^%s\s+([0-9.eE+-]+)\s*$" % re.escape(k), t, re.M))
    failed = {m.group(1): _f(m.group(2)) for m in re.finditer(r'^mockllm_failed_requests_total\{reason="([^"]+)"\}\s+([0-9.eE+-]+)', t, re.M)}
    return dict(completed=g("mockllm_completed_requests_total"), cancelled=g("mockllm_cancelled_requests_total"),
                failed=failed, active_final=g("mockllm_active_requests"))


def prom_peak(p):
    if not p.exists(): return None
    try:
        d = json.loads(p.read_text())
        vals = [float(v[1]) for r in d["data"]["result"] for v in r["values"]]
        if not vals: return None
        h = len(vals)//2 or 1
        return dict(n=len(vals), min=min(vals), max=max(vals), mean=sum(vals)/len(vals),
                    first_half_mean=sum(vals[:h])/h, second_half_mean=sum(vals[h:])/(len(vals)-h or 1))
    except Exception: return None


def bindexc(p):
    ts = [l.split()[0] for l in p.read_text().splitlines()
          if "BindException" in l and "Can't assign requested address" in l]
    return dict(count=len(ts), timestamps=ts)


def main():
    rd = Path(sys.argv[1]); env = json.loads((rd / "environment.json").read_text())
    rate = env["target_rate"]
    k6 = k6_metrics(rd / "k6-summary.json")
    tl = timeline_peaks(rd / "two-leg-socket-timeline.csv")
    mock = mock_metrics(rd / "mock-metrics-final.txt")
    be = bindexc(rd / "gateway.log")
    k6_exit = (rd / "k6-exit-code.txt").read_text().strip()
    postflight = (rd / "postflight.txt").read_text().strip() if (rd / "postflight.txt").exists() else "?"

    A = tl.get("A", {}); B = tl.get("B", {}); U = tl.get("UNION", {}); I = tl.get("INTERSECT", {})
    union_peak = U.get("unique_src_ports_peak", 0)
    inter_peak = I.get("unique_src_ports_peak", 0)

    prom = {m: prom_peak(rd / f"prom-{m}.json") for m in
            ["virtual_tasks_active", "gateway_active_requests", "process_cpu_usage",
             "jvm_threads_live_threads", "tomcat_connections_current_connections",
             "process_files_open_files", "virtual_tasks_started_total"]}
    tomcat_peak = (prom["tomcat_connections_current_connections"] or {}).get("max", 0)
    vt_started = (prom["virtual_tasks_started_total"] or {}).get("max", 0)

    open_deficit = k6["client_sse_open_attempt"] - k6["client_sse_open_success"]
    open_deficit_frac = open_deficit / k6["client_sse_open_attempt"] if k6["client_sse_open_attempt"] else 0
    legB_deficit = vt_started - (mock["completed"] or 0)

    checks = {
        "k6_exit_zero": k6_exit == "0",
        "postflight_ok": postflight.endswith("OK"),
        "dropped_eq_0": k6["dropped_iterations"] == 0,
        "no_gateway_bindexception": be["count"] == 0,
        "no_k6_connect_failure": k6["client_zero_event_terminal"] == 0,
        "open_attempt_approx_success": open_deficit_frac <= 0.001,
        "mid_stream_eq_0": k6["client_failed_mid_stream"] == 0,
        "sse_error_eq_0": k6["client_sse_error"] == 0,
        "mock_no_cancel_or_fail": (mock["cancelled"] in (0, 0.0, None)) and not mock["failed"],
        "legB_deficit_eq_0": legB_deficit == 0,
        "tomcat_connections_non_binding": 0 < tomcat_peak < env["server_tomcat_max_connections"],
        "syn_sent_not_accumulating": max(A.get("syn_sent_peak", 0), B.get("syn_sent_peak", 0)) <= 5,
        "arrival_within_5pct": abs(k6["measurement_started"] - rate * env["measurement_sec"]) <= 0.05 * rate * env["measurement_sec"],
        # 4b ephemeral headroom
        "union_within_80pct_pool": union_peak <= HEADROOM_PORTS,
    }
    classification = "CONTROL_SAFE" if all(checks.values()) else "CONTROL_INVALID"

    result = {
        "calibration_unit": "8.2-single-host-safe-open-max-recalibration",
        "canonical": False, "target": "m2", "rate": rate, "run_label": env["run_label"],
        "classification": classification,
        "checks": checks,
        "ephemeral": {
            "pool": POOL, "headroom_frac": HEADROOM_FRAC, "headroom_ports": HEADROOM_PORTS,
            "legA_unique_ports_peak": A.get("unique_src_ports_peak", 0),
            "legB_unique_ports_peak": B.get("unique_src_ports_peak", 0),
            "union_unique_ports_peak": union_peak,
            "union_pct_of_pool": round(100.0 * union_peak / POOL, 2),
            "intersect_unique_ports_peak": inter_peak,
            "legA_time_wait_peak": A.get("time_wait_peak", 0),
            "legB_time_wait_peak": B.get("time_wait_peak", 0),
            "legA_established_peak": A.get("established_peak", 0),
            "legB_established_peak": B.get("established_peak", 0),
            "legA_syn_sent_peak": A.get("syn_sent_peak", 0),
            "legB_syn_sent_peak": B.get("syn_sent_peak", 0),
        },
        "k6": k6,
        "gateway_bindexception": be,
        "legB_deficit_vs_mock": legB_deficit,
        "open_attempt_minus_success": open_deficit,
        "mock": mock,
        "prometheus": prom,
    }
    (rd / "classification.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    print(f"\nR={rate}  ->  {classification}")
    print(f"  A∪B peak = {union_peak} / {POOL} ({result['ephemeral']['union_pct_of_pool']}%)  "
          f"[headroom line {HEADROOM_PORTS} = 80%]")
    failed = [k for k, v in checks.items() if not v]
    if failed: print("  failed checks:", ", ".join(failed))


if __name__ == "__main__":
    main()
