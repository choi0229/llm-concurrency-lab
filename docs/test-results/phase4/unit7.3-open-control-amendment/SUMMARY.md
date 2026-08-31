# Phase 4 Open Unit 7.3 — Final Connector + LoadGen Control Amendment

**Correction added by Unit 7.4** (`docs/test-results/phase4/unit7.4-client-connection-lifecycle-
forensics/SUMMARY.md`): §18's "client `established`(21186) + `time_wait`(3037) exceeds the
16384-port ephemeral range" arithmetic below is superseded, not deleted. The client-side socket
sampler was found to double-count established connections (client+server loopback row pairs both
matched); the corrected client-side occupancy (~10,593) does not clearly exceed the range. The root
cause of the residual `EADDRNOTAVAIL` failures is now understood to be a confirmed, source-verified
`xk6-sse` connection-lifecycle defect (never reuses or closes idle connections across iterations),
not a hard host ephemeral-port ceiling. See Unit 7.4 for the full corrected analysis.

**Regression FAILED at the ceiling level (§14/§15). No new canonical Open Screening epoch declared.
No Open Formal. No canonical Screening run.** All artifacts here are regression/probe evidence, none
promoted to `unit7-open-screening/`.

## 1. Case A final confirmation

Confirmed: **TOMCAT / GATEWAY FRONT-DOOR CONFIG LIMIT.** M2 R=160's original failure is not a Virtual
Thread execution-capacity finding. Additionally discovered in this Unit: it is not *only* a Gateway
config limit either — see §18-19 below, a second, host-level (client ephemeral port) ceiling exists
underneath it.

## 2. Tomcat effective keep-alive settings (source-confirmed, not memory)

`server.tomcat.keep-alive-timeout` is unset by Spring Boot (`TomcatServerProperties` leaves the field
`null`); Tomcat's own `AbstractEndpoint.getKeepAliveTimeout()` falls back to `getConnectionTimeout()`
when unset (confirmed via `javap` disassembly of `tomcat-embed-core-11.0.22.jar`). Effective
`connectionTimeout` = `SocketProperties.soTimeout` default = **20000ms (20s)**, confirmed via
`javap` of `SocketProperties`'s constructor bytecode (`sipush 20000` immediately preceding the
`soTimeout` field assignment) — **not** the "~60s" placeholder Unit 7.2 explicitly flagged as
unconfirmed.

## 3. Old maxConnections

**8192** — Spring Boot's `TomcatServerProperties` constructor default (confirmed via `javap`,
matching the live metric in every run this project has captured). `acceptCount=100`,
`maxKeepAliveRequests=100`, `threads.max=200` also confirmed the same way.

## 4. Open maxConnections headroom formula

```
EXPECTED_CONNECTION_LIFETIME_BUDGET_S = STREAM_DURATION_SLO_S(10.0) + KEEPALIVE_TIMEOUT_S(20.0) = 30.0
OPEN_TOMCAT_CONNECTION_BUDGET = SAFE_OPEN_MAX(256) * 30.0 = 7680
OPEN_TOMCAT_MAX_CONNECTIONS = ceil(7680 * 1.25) = 9600
```

**This formula's result (9600) was regression-tested and found insufficient** — see §14/§18. The
underlying arithmetic is disclosed as principled but incomplete, not wrong on its own terms: it
correctly derives a value from confirmed source constants, but the confirmed 20s keep-alive alone
does not fully explain the true connection-dwell pressure observed under load.

## 5. New M1/M2 maxConnections

**Provisionally 9600 in the canonical harness (`scripts/run-phase4-open-benchmark.sh`,
`SERVER_TOMCAT_MAX_CONNECTIONS`), applied identically to M1 and M2.** Not raised further despite the
regression failure, because a probe at a clearly-non-binding 50000 showed that simply raising this
value further does not reduce total failures — it shifts the dominant failure signature to a second,
host-level ceiling (§18) instead. Raising it without a decision on that second ceiling would not fix
anything. See `docs/decisions/phase4-open-tomcat-connector-headroom.md` §7 for the full evidence
chain.

## 6. Why maxThreads unchanged

`tomcat_threads_busy_threads` peaked at only 107/200 in the original R=160 diagnostic and 32-54/200
in this Unit's regression/probe runs — never remotely close to its ceiling in any run. No evidence
anywhere in this project's data implicates `maxThreads`.

## 7. Why acceptCount unchanged

No metric exposes it directly, and — critically — the 50000-ceiling probe (§18) drove client-side
`SYN_SENT` down to ~0 (from a peak of 1322/1246 at the lower, binding ceilings), proving the earlier
`SYN_SENT` accumulation was entirely a downstream artifact of `maxConnections` binding, not an
independent `acceptCount`/kernel-backlog constraint. This is now evidenced, not merely assumed.

## 8. Host safety check

`maxConnections` is an admission gate, not a pre-allocation (§6 of the ADR). Gateway FD ceiling
(1,048,576) and CPU (never above ~3.3% mean in any run this Unit) are far from any risk. The
*client's* ephemeral port space (16384) is the actual constraint that surfaced — see §18.

## 9. M3 Open pool final config

Unchanged: `OPEN_M3_MAX_CONNECTIONS=3200` / `OPEN_M3_PENDING_ACQUIRE_MAX_COUNT=3200`
(`docs/test-plan/phase4-open-screening-protocol.md` §2.2), confirmed still in effect, not re-tuned,
per the explicit "결과 보고 재튜닝 금지" instruction. No conflict found.

## 10. M1 R=10 VU audit

`vus.max=450` in the original `m1-r10-screen1-postvufix` artifact — **did not reach** the old
`MAX_VUS=800` ceiling. Root cause was VU-pool *growth-pacing*, not ceiling sizing: `PRE_ALLOCATED_
VUS=200` was far below the 450 actually needed once M1 began degrading (`MODEL_TIMEOUT`,
`executor_queue_depth` peaking at 400/500), and k6's live dynamic growth from 200 toward 450 couldn't
keep pace with the burst.

## 11. Dropped root cause

LoadGen VU-pool growth-pacing lag during a genuine, independent M1 reliability event — not a Gateway
finding, not (primarily) a static ceiling-sizing problem, though the ceiling was also too tight to
serve as a comfortable pre-allocated base.

## 12. Final preAllocatedVUs formula

```
VU_TIMEOUT_S = CHAT_TOTAL_TIMEOUT_MS / 1000 = 60
PRE_ALLOCATED_VUS = min(RATE * 60, 20000)
```

Scales with each run's own `RATE` (not `SAFE_OPEN_MAX`), pre-allocating up to the app's own
worst-case timeout bound so a sudden degradation never requires live VU-pool growth at all.

## 13. Final maxVUs formula

```
MAX_VUS = min(PRE_ALLOCATED_VUS * 5 / 4, 25000)
```

A true emergency backstop, 25% above the pre-allocated pool, capped defensively well above anything
the frozen 2–256 rate range should ever need.

## 14. LoadGen safety validation

Direct-Mock dry-run at `RATE=256` (frozen range top), `PRE_ALLOCATED_VUS=15360`, `MAX_VUS=19200`:
zero gap, zero drops, actual peak VU usage only 2146 (matches Little's Law nominal estimate almost
exactly), k6 RSS peaked ~3.05GB on a 16GB host (not a safety risk), CPU 28–39% of one core. Validated
safe across the frozen rate range. Full detail: `docs/decisions/phase4-open-loadgen-vu-sizing.md`.

## 15. M1 regression result

`m1-r2-regression1`: clean, GREEN, `dropped=0`. `m1-r10-regression1`: **`dropped_iterations=0`**
(the specific bug is fixed) — `vus.max=550` stayed within the new `PRE_ALLOCATED_VUS=600` without any
live growth needed. Classification came back `MODEL_REJECTION` (`rejected=53`) rather than the
original run's `MODEL_TIMEOUT` — run-to-run variance in a genuine RED finding, not forced to match
the prior result (per the explicit "expected 결과로 만들지 않는다" instruction).

## 16. M1 R10 dropped=0 여부

**Yes — confirmed 0** in the regression run, with actual VU usage (550) comfortably inside the new
pre-allocated pool (600), no live growth required.

## 17. M2 sanity result

`m2-r40-regression1`: clean, GREEN, `dropped=0`, `tomcat_connections_current` peak 2757 — comfortably
non-binding against the new 9600 ceiling at this lower rate.

## 18. M2 R160 diagnostic result (the regression + the follow-up probe)

- **`m2-r160-regression1`** (canonical formula, `maxConnections=9600`): **FAILED.**
  `tomcat_connections_current_connections` still plateaued at 9600.0 (110/188 samples ≥99% of
  ceiling); `open_attempt=28800` vs `open_success=26120` (gap 2680, down from ~5400 at the old 8192
  but not eliminated); identical `dial tcp ...: operation timed out` signature recurred.
- **`m2-r160-probe-uncapped`** (deliberately non-binding `maxConnections=50000`, probe only, not a
  candidate final value): Tomcat itself clean (`tomcat_connections_non_binding=True`, peak 10549),
  `SYN_SENT` collapsed to ~0 — but **1129 residual `zero_event_terminal` failures** with a **different**
  signature: `dial tcp ...: connect: can't assign requested address` (client ephemeral-port
  exhaustion, near-instant failures, median 1ms elapsed) — client `established` (21186) +
  `time_wait` (3037) exceeded the 16384-port ephemeral range.

## 19. Tomcat current/max after amendment

At `maxConnections=9600` (the currently-applied canonical value): peak 9600.0 (bound). At the
50000 probe: peak 10549.0, `non_binding=True`. The TRUE organic Tomcat-side peak at R=160 for this
exact workload is therefore **≈10549** — informative for a future formula revision, but not adopted
as a new final canonical value in this Unit (§5).

## 20. Dial timeout recurrence 여부

**Yes, at 9600** (2680 occurrences, identical signature to the original). **No** at 50000 (0
occurrences of that specific signature), but replaced by 1129 occurrences of the client
ephemeral-port-exhaustion signature instead — total residual failures reduced (2680→1129) but not
eliminated by raising the ceiling alone.

## 21. M3 smoke result

`m3-r2-regression1`: clean, GREEN, `dropped=0`. New VU formula introduces no regression for M3
(Reactor Netty, unaffected by the Tomcat changes; only the VU-sizing change is common code, and it
did not disturb M3's own behavior at this smoke rate).

## 22. Driver/collector synthetic tests

`scripts/test_phase4_open_screening_driver_synthetic.py`: still **ALL PASS** (5/5) after adding
`tomcat_connections_non_binding` to `NO_RETRY_KEYS` in `run_phase4_open_screening.py` — the
UNEXPLAINED-STOP behavior approved in Unit 7.1 is intact and unaffected by this Unit's changes.

## 23. Open FINAL harness freeze status

**NOT frozen — blocked on §18's regression failure.** Changes made this Unit (`scripts/run-phase4-
open-benchmark.sh`: `SERVER_TOMCAT_MAX_CONNECTIONS`, new VU formula, added Tomcat Prometheus queries,
`server_tomcat_max_connections` in `environment.json`; `scripts/collect_phase4_open_result.py`:
`tomcat_connections_non_binding` validity check; `scripts/run_phase4_open_screening.py`:
`tomcat_connections_non_binding` added to `NO_RETRY_KEYS`) are real, tested, and an improvement (the
M1 dropped-iterations bug is genuinely fixed; M2's failure rate at R=160 is genuinely reduced), but
per §15's explicit "하나라도 실패: Open new epoch 시작 금지," the connector-ceiling regression
criterion did not fully pass, so these files are not declared FINAL/frozen yet.
`05-phase4-open-arrival-rate.js` required no changes (config-passing only, as instructed) and is
unaffected either way.

## 24. New logical epoch name

**Not declared.** Per §23, no new canonical Open Screening epoch can be started until §18's
unresolved second ceiling is addressed (see §25/26).

## 25. Full R2 restart recommendation 유지 여부

**Recommendation stands, unchanged from Unit 7.2's SUMMARY**: once the amendment is genuinely
complete, re-screen M1/M2/M3 from R=2 under final, frozen semantics, keeping existing clean R2–80 raw
as history/comparison evidence, not auto-promoted. This Unit's regression failure does not change
that recommendation — if anything it reinforces it, since the harness has now changed materially
again (Tomcat headroom + VU formula) since those R2–80 points were captured.

## 26. Open Screening resume readiness

**FAIL.** Two things block resumption:

1. **The connector/loadgen ceiling question is not closed.** Raising `maxConnections` alone trades
   one failure signature (Gateway dial-timeout) for another (client ephemeral-port exhaustion) at
   R=160 without reducing it to zero. A genuinely clean, fully-non-binding configuration for the top
   of the frozen Open rate range (up to `SAFE_OPEN_MAX=256`) likely requires addressing the client
   host's ephemeral port supply (`net.inet.ip.portrange.first/last`) and/or TIME_WAIT duration
   (`net.inet.tcp.msl`) — a **host-level, not repo-scoped, change** that was not authorized in this
   Unit's instructions and has **not** been made. Options for how to proceed (not decided here):
   (a) authorize a specific, disclosed sysctl change and re-validate; (b) accept a lower effective
   rate ceiling for Open Screening on this specific host than `SAFE_OPEN_MAX=256` (i.e., revisit
   Unit 3's SAFE_OPEN_MAX itself, now that a host-level confound in the original calibration is
   suspected — Unit 3's Direct-Mock-only calibration would not have hit this, since Direct-Mock's own
   connection dwell time is much shorter than through the Gateway, per the established/time_wait
   comparison in the ADR §7); (c) some other mitigation (e.g., if `k6/x/sse`'s underlying transport
   can be configured for connection reuse, reducing per-iteration port consumption — not
   investigated in this Unit).
2. Per §23, harness/collector/driver semantics are consequently not yet FINAL-frozen.

M1's dropped-iterations fix (§10-16) and the collector/driver hardening (§22-23's non-regression
parts) are genuine, validated progress and do not need to be redone — only the connector-ceiling
question remains open.
