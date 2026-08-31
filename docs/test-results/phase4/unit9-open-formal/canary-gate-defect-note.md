# Open Formal Stability Canary — gate defect disclosure (NOT a measurement finding)

**The M2 R168 Canary RUN is a clean PASS on every substantive dimension.** It was reported FAIL by
`scripts/run_phase4_open_formal.py` solely because of a defect in one gate check that I wrote.
Disclosed here in full; no measurement semantics are affected.

## The RUN (VALID, GREEN, transport-stable over the full 300 s Formal window)

`docs/test-results/phase4/unit9-open-formal/m2-r168-canary/` · warmup 120 s / measurement 300 s.

| dimension | value |
|---|---|
| valid | true (0 invalid reasons) |
| color / classification | **GREEN / GREEN** |
| cohort | started 50401 = completed 50401; rejected 0, failed_mid 0, failed_no_event 0; `invariant_ok` true |
| dropped_iterations | 0 |
| actual arrival | 168.003 /s (target 168; 50401 / 50400) |
| completion ratio | 1.0000 |
| TTFC p95 (completed) | 1.025 s (≤ 2.0) |
| stream duration p95 (completed) | 8.044 s (≤ 10.0) |
| clock integrity | ok (custom/builtin ratio 1.01) |
| backlog (virtual_tasks_active) | 1st-half 1335.8 → 2nd-half 1293.8, **not rising** |
| virtual_tasks_active peak | 1362 |
| platform threads peak | 109 (flat) |
| Tomcat connections peak | **1364 / 3200** → non-binding |
| CPU | mean 0.385 cores of 10 |
| RSS peak | 1.23 GiB |
| FD open peak | 2740 / 1 048 576 |
| BindException / EADDRNOTAVAIL | **0 / 0** |
| server_outcomes | upstream_error 0, timeout 0, internal_error 0, client_disconnect 0 |
| Mock | completed 70561 = virtual_tasks_started 70561; cancelled 0 |
| stall_detected | false |
| postflight_clean | true |

Light transport telemetry (5 s cadence, `light-transport-telemetry.csv`) across the whole run:
`time_wait` **stabilised at ~10 000–10 300 for the entire 300 s measurement window** (samples:
10149, 10131, 10069, 10306, 10269, 10217, 10124, 10080, 10035, 10329, 10302, 10209 — flat, ~300-wide
band, **zero upward trend**). `est_to_18102` and `est_to_8000` both flat at ~2 650–2 710 and equal
every sample (LEG A ≈ LEG B). This is exactly the R=168 occupancy Unit 8.2 measured and classified
**CONTROL_SAFE** (`A∪B` 13 059 = 79.71 % of the 16 384 pool; TWa 5234 + TWb 5149 ≈ 10 383).
Host TIME_WAIT is **0 now** — fully returned to baseline after the 2·MSL (30 s) decay.

## The defect

The gate check `time_wait_returns_toward_baseline` sampled the host TIME_WAIT count **once,
immediately at drain-end**, and required it to be `≤ 3000`. At R=168 the end-of-run TIME_WAIT
residue is ~10 k (Unit 8.2: TWa + TWb ≈ 10 383) — the *expected steady state*, which then decays
over 2·MSL = 30 s. Sampling before that decay and expecting ≤ 3000 is wrong on both the timing and
the threshold. Recorded value: `time_wait_at_drain_end = 9781` → check failed → Canary reported FAIL.

**None of the protocol §11 STOP triggers occurred** (no INVALID, no non-GREEN, no `dropped > 0`, no
clock anomaly, no transport error, no Tomcat binding, no EADDRNOTAVAIL, no postflight leak, no
unexplained divergence). The only failing item is this self-inflicted gate check.

## Fix applied to `scripts/run_phase4_open_formal.py` (code only — NOT re-run)

`time_wait_returns_toward_baseline` now samples TIME_WAIT **75 s after drain-end** (well past
2·MSL) and requires `≤ 1500`. The snapshot json now records
`time_wait_before_run` / `time_wait_at_drain_end` / `time_wait_75s_post_drain`. No measurement
semantics changed; only the Canary's own post-run host-hygiene check.

Per protocol §11 / §13 the Canary is **not auto-retried** and the matrix is **not auto-started**.
Awaiting the user's decision: (a) re-run the Canary once with the corrected gate, or (b) accept the
Canary on its substantive merits (clean VALID GREEN, transport stable across the full 300 s window,
host fully drained) and authorise the 12-run matrix.
