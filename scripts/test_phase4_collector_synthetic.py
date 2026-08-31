#!/usr/bin/env python3
"""Synthetic fixture tests for collect_phase4_closed_result.py (Unit 4.5/4.6 briefs section 10 /
20-24). Builds fake run directories with known values, runs the real collector, asserts specific
computed fields. No Gateway/Mock/k6 process is started.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
COLLECTOR = SCRIPT_DIR / "collect_phase4_closed_result.py"


def write(path, content):
    path.write_text(content if isinstance(content, str) else json.dumps(content))


def prom_matrix(values):
    return {"status": "success", "data": {"resultType": "matrix", "result": [{"metric": {}, "values": values}]}}


def base_fixture(tmp: Path, model="m1", N=20, actual_started=None, completed=0, rejected=0,
                  failed_mid=0, failed_noevt=0, prewarm_count=5,
                  server_completed_delta=None, server_rejected=0.0, server_timeout=0.0,
                  server_upstream_error=0.0, server_client_disconnect=0.0, server_internal_error=0.0,
                  completed_ttfc_p95=1.02, completed_dur_p95=7.9,
                  gateway_active_final=0.0, mock_active_final=0.0, pid_match=True,
                  write_overflow=0.0):
    if actual_started is None:
        actual_started = N
    if server_completed_delta is None:
        server_completed_delta = completed + prewarm_count

    base = 1900000000
    write(tmp / "environment.json", {"model": model, "concurrency": N, "run_label": "synthetic",
                                      "gateway_pid": 12345, "prewarm_count": prewarm_count})
    write(tmp / "timestamps.json", {"process_start_ms": base * 1000 - 2000, "k6_wall_start_ms": base * 1000,
                                     "k6_wall_end_ms": (base + 10) * 1000, "drain_start_ms": (base + 10) * 1000,
                                     "drain_end_ms": (base + 10) * 1000 + 50})
    write(tmp / "k6-summary.json", {
        "metrics": {
            "client_actual_started_total": {"count": actual_started},
            "client_stream_completed_total": {"count": completed},
            "client_rejected_total": {"count": rejected},
            "client_failed_mid_stream_total": {"count": failed_mid},
            "client_failed_no_event_total": {"count": failed_noevt},
            "dropped_iterations": {"count": 0},
            "client_completed_ttfc_seconds": {"avg": completed_ttfc_p95 - 0.02, "p(95)": completed_ttfc_p95},
            "client_completed_stream_duration_seconds": {"avg": completed_dur_p95 - 0.05, "p(95)": completed_dur_p95},
            "client_ttfc_seconds": {"avg": 0.5, "p(95)": 0.9},
            "client_stream_duration_seconds": {"avg": 3.0, "p(95)": 5.0},
            "client_wave_start_epoch_ms": {"min": base * 1000, "max": base * 1000 + 5},
        }
    })
    write(tmp / "k6-exit-code.txt", "0\n")
    write(tmp / "pid-verification.txt", f"GATEWAY_PID=12345\nPID_MATCH={'true' if pid_match else 'false'}\n")

    def outcome_lines(vals):
        return "".join(f'gateway_requests_total{{outcome="{k}"}} {v}\n' for k, v in vals.items())

    before = {"completed": 0.0, "rejected": 0.0, "timeout": 0.0, "upstream_error": 0.0, "client_disconnect": 0.0, "internal_error": 0.0, "write_overflow": 0.0}
    after = dict(before)
    after["completed"] = server_completed_delta
    after["rejected"] = server_rejected
    after["timeout"] = server_timeout
    after["upstream_error"] = server_upstream_error
    after["client_disconnect"] = server_client_disconnect
    after["internal_error"] = server_internal_error
    after["write_overflow"] = write_overflow

    write(tmp / "gateway-metrics-before-wave.txt", outcome_lines(before) + "gateway_active_requests 0.0\ngateway_upstream_active 0.0\n")
    write(tmp / "gateway-metrics-final.txt", outcome_lines(after) +
          f"gateway_active_requests {gateway_active_final}\ngateway_upstream_active 0.0\n"
          "executor_rejected_total 0.0\n"
          'servlet_write_overflow_total{source="perstream"} 0.0\n'
          'servlet_write_overflow_total{source="executor"} 0.0\n')
    write(tmp / "mock-metrics-final.txt", f"mockllm_active_requests {mock_active_final}\nmockllm_waiting_requests 0.0\nmockllm_completed_requests_total {server_completed_delta}\n")
    write(tmp / "mock.log", "2026-08-27 20:00:00,000 INFO [x] stream requested: chunkCount=35 firstChunkDelayMs=1000\n"
                             "2026-08-27 20:00:07,850 INFO [x] completed\n")

    with open(tmp / "rss-samples.csv", "w") as f:
        f.write("epoch_s,rss_kib,pcpu\n")
        for i, rss in enumerate([200000, 210000, 220000, 215000]):
            f.write(f"{base + i},{rss},1.5\n")

    write(tmp / "prom-jvm_threads_live_threads.json", prom_matrix([[base + i, "50"] for i in range(10)]))
    write(tmp / "prom-process_cpu_usage.json", prom_matrix([[base, "0.1"], [base + 1, "0.2"], [base + 2, "0.3"]]))
    write(tmp / "prom-system_cpu_count.json", prom_matrix([[base, "10"]]))
    write(tmp / "prom-process_files_open_files.json", prom_matrix([[base, "50"], [base + 1, "70"]]))
    write(tmp / "prom-process_files_max_files.json", prom_matrix([[base, "1048576"]]))
    write(tmp / "prom-jvm_memory_used_bytes.json", prom_matrix([[base, "1000000"]]))
    write(tmp / "prom-executor_active.json", prom_matrix([[base, "5"], [base + 1, "12"]]))
    write(tmp / "prom-executor_queue_depth.json", prom_matrix([[base, "0"]]))


def run_collector(tmp):
    subprocess.run([sys.executable, str(COLLECTOR), str(tmp)], check=True, capture_output=True)
    return json.loads((tmp / "result.json").read_text())


# Unit 5.3 pending-validity amendment fixtures (docs/test-results/phase4/
# unit5.3-m3-pending-validity-amendment/SUMMARY.md). base_fixture's wave window for model="m3" is
# always [base, base+10] (client_wave_start_epoch_ms.min=base*1000, k6_wall_end_ms=(base+10)*1000);
# process_start is base-2, drain is base+10..base+10.05 -- so t<base is "prewarm/startup" and
# t>base+10 is "drain", purely from the fixture's own existing timestamps, matching how the real
# harness lays these out (Unit 5.2 forensics).
def prom_multi_series(series_list):
    """series_list: list of (metric_dict, values) -- for testing unexpected multi-series responses."""
    return {"status": "success", "data": {"resultType": "matrix",
                                           "result": [{"metric": m, "values": v} for m, v in series_list]}}


M3_DEFAULT_POOL_IDENTITY = {"id": "1", "name": "phase4-webflux-pool", "remote_address": "127.0.0.1:8000"}


def m3_reactor_fixture(tmp: Path, base: int, pending_samples, active_samples, max_connections=800.0,
                        pending_metric=None, active_metric=None, max_metric=None,
                        omit_pending=False, omit_active=False, omit_max_connections=False,
                        extra_pending_series=None, extra_active_series=None, extra_max_series=None):
    """pending_samples/active_samples: list of (offset_seconds_from_base, value), or None to omit
    that metric file entirely (Unit 5.3.1/5.3.2 closure missing-metric fixtures).
    pending_metric/active_metric/max_metric: label dict override (default: identical pool identity
    on all three, matching real M3 raw evidence) -- pass a different dict to test identity mismatch.
    extra_*_series: additional (metric, values)/(labels, value) tuples for unexpected cardinality.
    """
    pending_metric = pending_metric or dict(M3_DEFAULT_POOL_IDENTITY)
    active_metric = active_metric or dict(M3_DEFAULT_POOL_IDENTITY)
    max_metric = max_metric or dict(M3_DEFAULT_POOL_IDENTITY)

    gw_path = tmp / "gateway-metrics-final.txt"
    if not omit_max_connections:
        labelstr = ",".join(f'{k}="{v}"' for k, v in max_metric.items())
        lines = [f'reactor_netty_connection_provider_max_connections{{{labelstr}}} {max_connections}\n']
        for labels, value in (extra_max_series or []):
            extra_labelstr = ",".join(f'{k}="{v}"' for k, v in labels.items())
            lines.append(f'reactor_netty_connection_provider_max_connections{{{extra_labelstr}}} {value}\n')
        gw_path.write_text(gw_path.read_text() + "".join(lines))

    if not omit_pending:
        series = [(pending_metric, [[base + off, str(v)] for off, v in pending_samples])]
        series += (extra_pending_series or [])
        write(tmp / "prom-reactor_netty_connection_provider_pending_connections.json", prom_multi_series(series))
    if not omit_active:
        series = [(active_metric, [[base + off, str(v)] for off, v in active_samples])]
        series += (extra_active_series or [])
        write(tmp / "prom-reactor_netty_connection_provider_active_connections.json", prom_multi_series(series))


def main():
    failures = []

    def check(name, cond):
        status = "PASS" if cond else "FAIL"
        print(f"[{status}] {name}")
        if not cond:
            failures.append(name)

    # ---- 1. Clean GREEN fixture (baseline, mirrors real N=20 runs) ----
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base_fixture(tmp, N=20, actual_started=20, completed=20)
        r = run_collector(tmp)
        check("clean fixture: valid=True", r["valid"] is True)
        check("clean fixture: color=GREEN", r["model_outcome"]["color"] == "GREEN")
        check("clean fixture: cohort invariant true", r["client_cohort"]["invariant_ok"] is True)
        check("clean fixture: target_reached true", r["client_cohort"]["target_reached"] is True)

    # ---- 2. Section 20: actual_started < target -> target_reached=false -> INVALID ----
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base_fixture(tmp, N=100, actual_started=80, completed=80, server_completed_delta=80 + 5)
        r = run_collector(tmp)
        check("actual_started(80) < target(100): cohort invariant still true (80==80)", r["client_cohort"]["invariant_ok"] is True)
        check("actual_started(80) < target(100): target_reached=False", r["client_cohort"]["target_reached"] is False)
        check("actual_started(80) < target(100): valid=False", r["valid"] is False and "target_reached" in r["invalid_reasons"])

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base_fixture(tmp, N=100, actual_started=100, completed=100)
        r = run_collector(tmp)
        check("actual_started==target(100): target_reached=True", r["client_cohort"]["target_reached"] is True)

    # ---- 3. Section 21: completed-only latency population (rejected duration must not pollute p95) ----
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # 50 completed (dur~8s), 50 rejected (near-instant, would drag aggregate down if mixed in)
        base_fixture(tmp, N=100, actual_started=100, completed=50, rejected=50,
                      server_completed_delta=50 + 5, server_rejected=50.0,
                      completed_ttfc_p95=1.05, completed_dur_p95=8.0)
        r = run_collector(tmp)
        check("completed-only duration p95 ~8s (unaffected by 50 near-instant rejections)",
              abs(r["client_latency"]["completed_duration_p95_s"] - 8.0) < 0.01)
        check("50/100 rejected -> reliability_pass=False (RED, not GREEN)", r["model_outcome"]["reliability_pass"] is False)
        check("50/100 rejected -> classification=MODEL_REJECTION", r["model_outcome"]["classification"] == "MODEL_REJECTION")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base_fixture(tmp, N=20, actual_started=20, completed=0, failed_noevt=20,
                      server_completed_delta=0 + 5, server_timeout=20.0)
        r = run_collector(tmp)
        check("completed=0 -> completed_ttfc_p95_s is null", r["client_latency"]["completed_ttfc_p95_s"] is None)
        check("completed=0 -> completed_duration_p95_s is null", r["client_latency"]["completed_duration_p95_s"] is None)
        check("completed=0, all failed_no_event -> still valid (RED is a valid measurement)", r["valid"] is True)

    # ---- 4. Section 22: timeout classification (server evidence required) ----
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base_fixture(tmp, N=100, actual_started=100, completed=80, failed_noevt=20,
                      server_completed_delta=80 + 5, server_timeout=20.0)
        r = run_collector(tmp)
        check("timeout fixture: valid=True", r["valid"] is True)
        check("timeout fixture: classification=MODEL_TIMEOUT", r["model_outcome"]["classification"] == "MODEL_TIMEOUT")
        check("timeout fixture: reliability_pass=False", r["model_outcome"]["reliability_pass"] is False)

    # ---- 5. Section 23: rejection classification (client+executor+server all agree) ----
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base_fixture(tmp, N=600, actual_started=600, completed=550, rejected=50,
                      server_completed_delta=550 + 5, server_rejected=50.0)
        r = run_collector(tmp)
        check("rejection fixture: valid=True", r["valid"] is True)
        check("rejection fixture: classification=MODEL_REJECTION", r["model_outcome"]["classification"] == "MODEL_REJECTION")

    # ---- 6. Section 24: unknown failure -- no server evidence for the client-side failure ----
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base_fixture(tmp, N=50, actual_started=50, completed=40, failed_noevt=10,
                      server_completed_delta=40 + 5)  # server_timeout/rejected/upstream_error all default 0
        r = run_collector(tmp)
        check("no server evidence for client failure -> classification=UNCLASSIFIED_MODEL_FAILURE (not forced MODEL_TIMEOUT)",
              r["model_outcome"]["classification"] == "UNCLASSIFIED_MODEL_FAILURE")
        check("unclassified failure fixture still valid=True (it's a real, if unexplained, model result)", r["valid"] is True)

    # ---- 7. Prewarm contamination detection ----
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # server completed delta should be completed+prewarm(5); deliberately wrong by 3 to simulate contamination
        base_fixture(tmp, N=20, actual_started=20, completed=20, server_completed_delta=20 + 5 - 3)
        r = run_collector(tmp)
        check("prewarm contamination mismatch -> valid=False", r["valid"] is False and "prewarm_contamination_ok" in r["invalid_reasons"])

    # ---- 8. Existing Unit 4.5 checks retained ----
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base_fixture(tmp, N=20, actual_started=20, completed=20, pid_match=False)
        r = run_collector(tmp)
        check("PID mismatch -> valid=False", r["valid"] is False and "pid_match" in r["invalid_reasons"])

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base_fixture(tmp, N=700, actual_started=700, completed=700, server_completed_delta=700 + 5)
        r = run_collector(tmp)
        check("N > SAFE_CLOSED_MAX(640) -> valid=False", r["valid"] is False and "control_range_ok" in r["invalid_reasons"])

    # ---- 9. Unit 5.3 M3 pending-validity amendment: Case 1 -- clean, no pending ----
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base = 1900000000
        base_fixture(tmp, model="m3", N=400, actual_started=400, completed=400, server_completed_delta=400 + 5)
        m3_reactor_fixture(tmp, base,
                            pending_samples=[(i, 0) for i in range(11)],
                            active_samples=[(0, 1)] + [(i, 400) for i in range(1, 11)])
        r = run_collector(tmp)
        pool = r["implementation_specific"]["reactor_connection_pool"]
        check("Case1 clean: valid=True", r["valid"] is True)
        check("Case1 clean: sustained_pending=False", pool["sustained_pending"] is False)
        check("Case1 clean: connection_limit_binding=False", pool["connection_limit_binding"] is False)
        check("Case1 clean: pending_peak_wave=0", pool["pending_peak_wave"] == 0)

    # ---- 10. Case 2 -- transient ramp-up pending (the actual M3 N=400 screen1 semantic, NOT hardcoded from that artifact) ----
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base = 1900000000
        base_fixture(tmp, model="m3", N=400, actual_started=400, completed=400, server_completed_delta=400 + 5)
        m3_reactor_fixture(tmp, base,
                            pending_samples=[(0, 0), (1, 1), (2, 0), (3, 0), (4, 0), (5, 0), (6, 0), (7, 0), (8, 0), (9, 0), (10, 0)],
                            active_samples=[(0, 1), (1, 1)] + [(i, 400) for i in range(2, 11)])
        r = run_collector(tmp)
        pool = r["implementation_specific"]["reactor_connection_pool"]
        check("Case2 transient: valid=True (pending alone is not invalidity)", r["valid"] is True)
        check("Case2 transient: m3_connection_limit_binding_absent not in invalid_reasons",
              "m3_connection_limit_binding_absent" not in r["invalid_reasons"])
        check("Case2 transient: sustained_pending=False (1 consecutive sample)", pool["sustained_pending"] is False)
        check("Case2 transient: connection_limit_binding=False", pool["connection_limit_binding"] is False)
        check("Case2 transient: pending_max_consecutive_samples_wave=1", pool["pending_max_consecutive_samples_wave"] == 1)

    # ---- 11. Case 3 -- sustained pending but pool never reaches configured cap ----
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base = 1900000000
        base_fixture(tmp, model="m3", N=400, actual_started=400, completed=400, server_completed_delta=400 + 5)
        m3_reactor_fixture(tmp, base,
                            pending_samples=[(0, 0), (1, 1), (2, 1), (3, 1), (4, 1), (5, 0), (6, 0), (7, 0), (8, 0), (9, 0), (10, 0)],
                            active_samples=[(i, 400) for i in range(11)],  # never reaches configured 800
                            max_connections=800.0)
        r = run_collector(tmp)
        pool = r["implementation_specific"]["reactor_connection_pool"]
        check("Case3 sustained-below-cap: sustained_pending=True (4 consecutive)", pool["sustained_pending"] is True)
        check("Case3 sustained-below-cap: connection_limit_binding=False (active 400 < configured 800)", pool["connection_limit_binding"] is False)
        check("Case3 sustained-below-cap: valid=True (not forced INVALID)", r["valid"] is True)

    # ---- 12. Case 4 -- pool actually reaches configured cap with sustained pending -> CONTROL/CONFIGURATION LIMIT ----
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base = 1900000000
        base_fixture(tmp, model="m3", N=640, actual_started=640, completed=640, server_completed_delta=640 + 5)
        m3_reactor_fixture(tmp, base,
                            pending_samples=[(0, 0), (1, 5), (2, 5), (3, 5), (4, 5), (5, 0), (6, 0), (7, 0), (8, 0), (9, 0), (10, 0)],
                            active_samples=[(i, 800) for i in range(11)],
                            max_connections=800.0)
        r = run_collector(tmp)
        pool = r["implementation_specific"]["reactor_connection_pool"]
        check("Case4 cap-binding: sustained_pending=True", pool["sustained_pending"] is True)
        check("Case4 cap-binding: connection_limit_binding=True (active 800 == configured 800)", pool["connection_limit_binding"] is True)
        check("Case4 cap-binding: valid=False", r["valid"] is False)
        check("Case4 cap-binding: m3_connection_limit_binding_absent in invalid_reasons",
              "m3_connection_limit_binding_absent" in r["invalid_reasons"])

    # ---- 13. Case 5 -- pending only during prewarm (before wave_start=base), wave itself clean ----
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base = 1900000000
        base_fixture(tmp, model="m3", N=400, actual_started=400, completed=400, server_completed_delta=400 + 5)
        m3_reactor_fixture(tmp, base,
                            pending_samples=[(-2, 1), (-1, 1)] + [(i, 0) for i in range(0, 11)],
                            active_samples=[(-2, 1), (-1, 1)] + [(0, 1)] + [(i, 400) for i in range(1, 11)])
        r = run_collector(tmp)
        pool = r["implementation_specific"]["reactor_connection_pool"]
        check("Case5 prewarm-only: pending_peak_wave=0 (prewarm samples excluded)", pool["pending_peak_wave"] == 0)
        check("Case5 prewarm-only: lifetime pending_peak still shows the prewarm event (diagnostic)",
              r["implementation_specific"]["reactor_connection_pending_peak"]["peak"] == 1)
        check("Case5 prewarm-only: connection_limit_binding=False", pool["connection_limit_binding"] is False)
        check("Case5 prewarm-only: valid=True", r["valid"] is True)

    # ---- Unit 5.3.1 temporal-coupling closure fixtures ----
    # ---- Case 7 -- temporal MISMATCH: sustained pending, but active only reaches the configured
    # cap AFTER the pending period ends (the exact independent-peak false-positive the closure
    # fixes: amendment brief §1's own example) ----
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base = 1900000000
        base_fixture(tmp, model="m3", N=400, actual_started=400, completed=400, server_completed_delta=400 + 5)
        m3_reactor_fixture(tmp, base,
                            pending_samples=[(0, 0), (1, 1), (2, 1), (3, 1), (4, 0), (5, 0), (6, 0), (7, 0), (8, 0), (9, 0), (10, 0)],
                            active_samples=[(0, 400), (1, 400), (2, 400), (3, 400), (4, 400), (5, 400), (6, 400), (7, 800)] + [(i, 800) for i in range(8, 11)],
                            max_connections=800.0)
        r = run_collector(tmp)
        pool = r["implementation_specific"]["reactor_connection_pool"]
        check("Case7 temporal-mismatch: sustained_pending=True (pending 1-3, 3 consecutive)", pool["sustained_pending"] is True)
        check("Case7 temporal-mismatch: connection_limit_binding=False (cap reached only at t=7, after pending ended at t=3)",
              pool["connection_limit_binding"] is False)
        check("Case7 temporal-mismatch: binding_max_consecutive_samples_wave=0", pool["binding_max_consecutive_samples_wave"] == 0)
        check("Case7 temporal-mismatch: valid=True (not forced INVALID by an unrelated later peak)", r["valid"] is True)

    # ---- Case 8 -- real concurrent binding at exactly the sustained threshold (3 consecutive) ----
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base = 1900000000
        base_fixture(tmp, model="m3", N=640, actual_started=640, completed=640, server_completed_delta=640 + 5)
        m3_reactor_fixture(tmp, base,
                            pending_samples=[(0, 0), (1, 5), (2, 5), (3, 5), (4, 0), (5, 0), (6, 0), (7, 0), (8, 0), (9, 0), (10, 0)],
                            active_samples=[(0, 800), (1, 800), (2, 800), (3, 800)] + [(i, 800) for i in range(4, 11)],
                            max_connections=800.0)
        r = run_collector(tmp)
        pool = r["implementation_specific"]["reactor_connection_pool"]
        check("Case8 real-binding: sustained_pending=True", pool["sustained_pending"] is True)
        check("Case8 real-binding: binding_max_consecutive_samples_wave=3", pool["binding_max_consecutive_samples_wave"] == 3)
        check("Case8 real-binding: connection_limit_binding=True", pool["connection_limit_binding"] is True)
        check("Case8 real-binding: valid=False", r["valid"] is False)
        check("Case8 real-binding: REACTOR_CONNECTION_LIMIT reason present",
              "m3_connection_limit_binding_absent" in r["invalid_reasons"])

    # ---- Case 9 -- mixed partial overlap: 3 consecutive pending, only 2 samples overlap with active>=cap ----
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base = 1900000000
        base_fixture(tmp, model="m3", N=640, actual_started=640, completed=640, server_completed_delta=640 + 5)
        m3_reactor_fixture(tmp, base,
                            pending_samples=[(0, 0), (1, 1), (2, 1), (3, 1), (4, 0), (5, 0), (6, 0), (7, 0), (8, 0), (9, 0), (10, 0)],
                            active_samples=[(0, 400), (1, 400), (2, 800), (3, 800)] + [(i, 800) for i in range(4, 11)],
                            max_connections=800.0)
        r = run_collector(tmp)
        pool = r["implementation_specific"]["reactor_connection_pool"]
        check("Case9 partial-overlap: sustained_pending=True (pending 1-3, 3 consecutive)", pool["sustained_pending"] is True)
        check("Case9 partial-overlap: binding_max_consecutive_samples_wave=2 (only t=2,3 overlap with cap)",
              pool["binding_max_consecutive_samples_wave"] == 2)
        check("Case9 partial-overlap: connection_limit_binding=False (2 < frozen threshold 3)", pool["connection_limit_binding"] is False)
        check("Case9 partial-overlap: valid=True", r["valid"] is True)

    # ---- Case 10 -- missing pending series entirely ----
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base = 1900000000
        base_fixture(tmp, model="m3", N=400, actual_started=400, completed=400, server_completed_delta=400 + 5)
        m3_reactor_fixture(tmp, base, pending_samples=None, active_samples=[(i, 400) for i in range(11)], omit_pending=True)
        r = run_collector(tmp)
        pool = r["implementation_specific"]["reactor_connection_pool"]
        check("Case10 missing-pending: connection_limit_evaluable=False", pool["connection_limit_evaluable"] is False)
        check("Case10 missing-pending: connection_limit_binding=None (not fabricated False)", pool["connection_limit_binding"] is None)
        check("Case10 missing-pending: valid=False", r["valid"] is False)
        check("Case10 missing-pending: m3_connection_metrics_evaluable in invalid_reasons",
              "m3_connection_metrics_evaluable" in r["invalid_reasons"])
        check("Case10 missing-pending: m3_connection_limit_binding_absent NOT in invalid_reasons (vacuous pass, distinct failure already flagged)",
              "m3_connection_limit_binding_absent" not in r["invalid_reasons"])

    # ---- Case 11 -- missing active series entirely ----
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base = 1900000000
        base_fixture(tmp, model="m3", N=400, actual_started=400, completed=400, server_completed_delta=400 + 5)
        m3_reactor_fixture(tmp, base, pending_samples=[(i, 0) for i in range(11)], active_samples=None, omit_active=True)
        r = run_collector(tmp)
        pool = r["implementation_specific"]["reactor_connection_pool"]
        check("Case11 missing-active: connection_limit_evaluable=False", pool["connection_limit_evaluable"] is False)
        check("Case11 missing-active: valid=False", r["valid"] is False)
        check("Case11 missing-active: m3_connection_metrics_evaluable in invalid_reasons",
              "m3_connection_metrics_evaluable" in r["invalid_reasons"])

    # ---- Case 12 -- missing/invalid configured max_connections ----
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base = 1900000000
        base_fixture(tmp, model="m3", N=400, actual_started=400, completed=400, server_completed_delta=400 + 5)
        m3_reactor_fixture(tmp, base, pending_samples=[(i, 0) for i in range(11)],
                            active_samples=[(i, 400) for i in range(11)], omit_max_connections=True)
        r = run_collector(tmp)
        pool = r["implementation_specific"]["reactor_connection_pool"]
        check("Case12 missing-maxconn: connection_limit_evaluable=False", pool["connection_limit_evaluable"] is False)
        check("Case12 missing-maxconn: valid=False", r["valid"] is False)

    # ---- Case 13 -- unexpected multiple series on the pending metric ----
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base = 1900000000
        base_fixture(tmp, model="m3", N=400, actual_started=400, completed=400, server_completed_delta=400 + 5)
        m3_reactor_fixture(tmp, base, pending_samples=[(i, 0) for i in range(11)],
                            active_samples=[(i, 400) for i in range(11)],
                            extra_pending_series=[({"id": "2", "name": "other-pool", "remote_address": "127.0.0.1:9999"},
                                                    [[base + i, "0"] for i in range(11)])])
        r = run_collector(tmp)
        pool = r["implementation_specific"]["reactor_connection_pool"]
        check("Case13 multi-series: pending_series_count=2", pool["pending_series_count"] == 2)
        check("Case13 multi-series: connection_limit_evaluable=False (no silent result[0])", pool["connection_limit_evaluable"] is False)
        check("Case13 multi-series: valid=False", r["valid"] is False)

    # ---- Case 14 -- pool-identity mismatch between pending and active series ----
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base = 1900000000
        base_fixture(tmp, model="m3", N=400, actual_started=400, completed=400, server_completed_delta=400 + 5)
        m3_reactor_fixture(tmp, base, pending_samples=[(i, 0) for i in range(11)],
                            active_samples=[(i, 400) for i in range(11)],
                            pending_metric={"id": "1", "name": "phase4-webflux-pool", "remote_address": "127.0.0.1:8000"},
                            active_metric={"id": "2", "name": "some-other-pool", "remote_address": "127.0.0.1:9999"})
        r = run_collector(tmp)
        pool = r["implementation_specific"]["reactor_connection_pool"]
        check("Case14 label-mismatch: series_identity_match=False", pool["series_identity_match"] is False)
        check("Case14 label-mismatch: connection_limit_evaluable=False", pool["connection_limit_evaluable"] is False)
        check("Case14 label-mismatch: valid=False", r["valid"] is False)

    # ---- Unit 5.3.2 connection-metric coverage closure fixtures ----
    # ---- Case 15 -- pending/active wave-window timestamp SETS differ (active missing one sample) ----
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base = 1900000000
        base_fixture(tmp, model="m3", N=400, actual_started=400, completed=400, server_completed_delta=400 + 5)
        m3_reactor_fixture(tmp, base,
                            pending_samples=[(0, 0), (1, 0), (2, 1), (3, 0), (4, 0)],
                            active_samples=[(0, 400), (1, 400), (3, 400), (4, 400)])  # t=2 missing on active
        r = run_collector(tmp)
        pool = r["implementation_specific"]["reactor_connection_pool"]
        check("Case15 timestamp-mismatch: timestamp_alignment_ok=False", pool["timestamp_alignment_ok"] is False)
        check("Case15 timestamp-mismatch: connection_limit_evaluable=False", pool["connection_limit_evaluable"] is False)
        check("Case15 timestamp-mismatch: valid=False", r["valid"] is False)
        check("Case15 timestamp-mismatch: m3_connection_metrics_evaluable in invalid_reasons",
              "m3_connection_metrics_evaluable" in r["invalid_reasons"])

    # ---- Case 16 -- series exist, but zero samples land inside the wave window (all prewarm/drain) ----
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base = 1900000000
        base_fixture(tmp, model="m3", N=400, actual_started=400, completed=400, server_completed_delta=400 + 5)
        m3_reactor_fixture(tmp, base,
                            pending_samples=[(-2, 0), (-1, 0), (11, 0), (12, 0)],   # all outside [base, base+10]
                            active_samples=[(-2, 1), (-1, 1), (11, 0), (12, 0)])
        r = run_collector(tmp)
        pool = r["implementation_specific"]["reactor_connection_pool"]
        check("Case16 empty-wave-coverage: common_wave_sample_count=0", pool["common_wave_sample_count"] == 0)
        check("Case16 empty-wave-coverage: connection_limit_evaluable=False (not silently 'no binding')",
              pool["connection_limit_evaluable"] is False)
        check("Case16 empty-wave-coverage: valid=False", r["valid"] is False)

    # ---- Case 17 -- unexpected multiple series on max_connections ----
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base = 1900000000
        base_fixture(tmp, model="m3", N=400, actual_started=400, completed=400, server_completed_delta=400 + 5)
        m3_reactor_fixture(tmp, base, pending_samples=[(i, 0) for i in range(11)],
                            active_samples=[(i, 400) for i in range(11)],
                            extra_max_series=[({"id": "2", "name": "other-pool", "remote_address": "127.0.0.1:9999"}, 100.0)])
        r = run_collector(tmp)
        pool = r["implementation_specific"]["reactor_connection_pool"]
        check("Case17 max-multi-series: max_connections_series_count=2", pool["max_connections_series_count"] == 2)
        check("Case17 max-multi-series: connection_limit_evaluable=False", pool["connection_limit_evaluable"] is False)
        check("Case17 max-multi-series: valid=False", r["valid"] is False)

    # ---- Case 18 -- max_connections identity mismatch vs. pending/active pool ----
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base = 1900000000
        base_fixture(tmp, model="m3", N=400, actual_started=400, completed=400, server_completed_delta=400 + 5)
        m3_reactor_fixture(tmp, base, pending_samples=[(i, 0) for i in range(11)],
                            active_samples=[(i, 400) for i in range(11)],
                            max_metric={"id": "9", "name": "wrong-pool", "remote_address": "127.0.0.1:1234"})
        r = run_collector(tmp)
        pool = r["implementation_specific"]["reactor_connection_pool"]
        check("Case18 max-identity-mismatch: max_connections_identity_match=False", pool["max_connections_identity_match"] is False)
        check("Case18 max-identity-mismatch: connection_limit_evaluable=False", pool["connection_limit_evaluable"] is False)
        check("Case18 max-identity-mismatch: valid=False", r["valid"] is False)

    # ---- Case 19 -- runtime max_connections value disagrees with frozen WEBCLIENT_MAX_CONNECTIONS(800) ----
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base = 1900000000
        base_fixture(tmp, model="m3", N=400, actual_started=400, completed=400, server_completed_delta=400 + 5)
        m3_reactor_fixture(tmp, base, pending_samples=[(i, 0) for i in range(11)],
                            active_samples=[(i, 400) for i in range(11)], max_connections=100.0)
        r = run_collector(tmp)
        pool = r["implementation_specific"]["reactor_connection_pool"]
        check("Case19 max-value-mismatch: max_connections_value_ok=False", pool["max_connections_value_ok"] is False)
        check("Case19 max-value-mismatch: connection_limit_evaluable=False", pool["connection_limit_evaluable"] is False)
        check("Case19 max-value-mismatch: valid=False", r["valid"] is False)

    # ---- 14. Case 6 -- pending only during drain (after wave_end=base+10), wave itself clean ----
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base = 1900000000
        base_fixture(tmp, model="m3", N=400, actual_started=400, completed=400, server_completed_delta=400 + 5)
        m3_reactor_fixture(tmp, base,
                            pending_samples=[(i, 0) for i in range(0, 11)] + [(11, 1), (12, 1)],
                            active_samples=[(0, 1)] + [(i, 400) for i in range(1, 11)] + [(11, 0), (12, 0)])
        r = run_collector(tmp)
        pool = r["implementation_specific"]["reactor_connection_pool"]
        check("Case6 drain-only: pending_peak_wave=0 (drain samples excluded)", pool["pending_peak_wave"] == 0)
        check("Case6 drain-only: lifetime pending_peak still shows the drain event (diagnostic)",
              r["implementation_specific"]["reactor_connection_pending_peak"]["peak"] == 1)
        check("Case6 drain-only: connection_limit_binding=False", pool["connection_limit_binding"] is False)
        check("Case6 drain-only: valid=True", r["valid"] is True)

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s): {failures}")
        sys.exit(1)
    print(f"ALL SYNTHETIC COLLECTOR TESTS PASSED")


if __name__ == "__main__":
    main()
