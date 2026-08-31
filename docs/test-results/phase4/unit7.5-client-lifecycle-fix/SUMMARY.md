# Phase 4 Open Unit 7.5 — xk6-sse Client Lifecycle Fix + Final Regression

**Fix implemented, regression PASSED, Open FINAL harness freeze DECLARED.** Canonical Open Screening
has **not** started (all runs below are smoke/regression, not canonical points). Open Formal not
run.

## 1. xk6-sse close semantic

`Client.Close()` (`sse.go` lines 271-276) runs `closeResponseBody()` (idempotent, `sync.Once`-guarded
— safe even if xk6-sse's own internal EOF handling already ran it), `cancelRequest()` (a
`context.CancelFunc`, idempotent per Go's own contract), and `httpClient.CloseIdleConnections()` (the
one step nothing else calls) — confirmed via direct source read, not assumed.

## 2. Exact code change

`load-test-k6/scenarios/05-phase4-open-arrival-rate.js` and `06-phase4-open-diagnostic-r160.js`:
added `let sseClientRef = null;`, set `sseClientRef = client;` as the first line of the `sse.open()`
setup callback, and immediately after the `sse.open()` call returns:

```js
if (sseClientRef !== null) {
    try { sseClientRef.close(); }
    catch (e) { console.log('sse client.close() cleanup error: ' + JSON.stringify(e)); }
}
```

No other line changed. Workload semantics (`firstChunkDelayMs`, `chunkIntervalMs`, `chunkCount`,
`chunkSizeBytes`, event counting, first-event measurement, client cohort taxonomy, timeouts,
warmup/measurement windows, arrival rates, `gracefulStop`, SLOs, Gateway/Mock source) — all
unchanged, confirmed by diff review.

## 3. Close exactly-once structure

A single, unconditional call site placed *after* `sse.open()` returns, not inside any event callback
— `sse.open()` is confirmed (by source read) to block until the stream already reached a terminal
state (clean EOF, `ctx.Done()`) or never acquired a client at all (connection failed before the
setup callback ran, in which case `sseClientRef` stays `null` and the call is skipped). This
structure is exactly-once by construction: there is no code path that reaches this line more than
once per iteration, and no code path where the stream can still be actively read when it executes.

## 4. Normal-completion regression

Verified via two ad hoc synthetic runs before any full regression (not saved as permanent artifacts,
per this Unit's own scope): (1) Direct-Mock, 21 iterations, clean completion, `sse_event=126`
received, no crash, no `cleanup error` log line. (2) Connect-failure path (port with nothing
listening): 11/11 iterations correctly `failed_no_event`, `sseClientRef` correctly stayed `null`
(close skipped, no crash), test completed in exactly its configured 2.0s window (no hang).

## 5. Error/cancel regression

A genuine **mid-stream** Go-level read error (distinct from clean EOF) is a separate, pre-existing
xk6-sse behavior worth disclosing: `Open()`'s main select loop's `readErrChan` case only calls
`client.handleEvent("error", ...)` and loops again, without returning or closing anything — and the
`readEvents` goroutine has already exited after sending to `errorChan`, so no further channel sends
will ever occur. In that specific (narrow, not observed in any of this project's real runs so far —
every real failure mode found has been either clean EOF, a 503, or a connect-stage failure before
the callback ever ran) scenario, `sse.open()` would not return until the VU's `ctx.Done()` fires at
test end, meaning this Unit's fix (placed after `sse.open()` returns) would not run any sooner for
that case either. This is an **upstream xk6-sse behavior**, not introduced or worsened by this fix,
and out of this Unit's scope to patch (that would be Unit 7.4's "Candidate A," not "Candidate B").
Not observed to occur in any regression run performed in this Unit.

## 6. Direct-Mock low-rate smoke

`direct-mock-r2-lowrate1`: `open_attempt=open_success=first_event=360`, `completed=240` (=
`measurement_iterations_started` exactly), `dropped=0`, zero failures. Endpoint-aware socket
telemetry (new, see §15): client `established` peak 17 ≈ `server_established` peak 17 (correct 1:1
correspondence, no more 2x double-count); client `time_wait` peak 63 > `server_time_wait` peak 0 —
the **client** is now the active closer (as intended: it calls `.close()` promptly after each stream
completes, ahead of the server's own keep-alive timeout).

## 7. Direct-Mock R=160 result

`direct-mock-r160-lifecyclefix1`: **fully clean.** `open_attempt=open_success=first_event=http_reqs
=28801`, `completed=19204` (= measurement iterations exactly), `dropped=0`. Client `established` peak
**1280** ≈ `server_established` peak 1280 — matches Little's Law on stream duration alone
(`160×8s=1280`) almost exactly. `time_wait` (client) peak 5001 vs `server_time_wait` peak 6 — client
is the active closer, as at low rate.

## 8. M1 low-rate smoke

`m1-r2-lifecyclefix1`: clean, GREEN, `dropped=0`, `completion_ratio=1.0`.

## 9. M1 R10 regression

`m1-r10-lifecyclefix1`: `valid=true`, `dropped_iterations=0` (fix holds), `color=RED`,
`classification=MODEL_REJECTION` (`rejected=53`, `failed_mid_stream=778`) — closely reproduces Unit
7.3's own M1 R=10 regression (`rejected=53`, `failed_mid_stream=796`) almost exactly, confirming this
is a genuine, reproducible M1 finding at R=10, not run-to-run noise, and confirming the client-
lifecycle fix introduces no new failure mode for M1. `vus.max=550`, unchanged from Unit 7.3, well
within `PRE_ALLOCATED_VUS=600`. **Not promoted to canonical.**

## 10. M2 low-rate smoke

`m2-r4-lifecyclefix1`: clean, GREEN, `dropped=0`, `completion_ratio=1.0`, Tomcat connections peak 33
(negligible at this rate).

## 11. M2 R160 regression

Two regression runs, both **fully PASS**:

- `m2-r160-lifecyclefix1` (probe ceiling `maxConnections=50000`, to measure the true organic
  population post-fix): `open_attempt=open_success=first_event=http_reqs=28800`,
  `completed=19200`(exact), `dropped=0`, `zero_event_terminal=0`, `sse_error=0`. Gateway
  `requests_started_total=virtual_tasks_started_total=gateway_outcome_completed=28800` (exact
  match). `postflight.txt=OK`. Clock integrity: `client_completed_stream_duration_seconds` avg
  7.875s vs `http_req_duration` avg 7.869s — matching almost exactly, no anomaly.
- `m2-r160-finalvalue1` (the adopted final ceiling `maxConnections=3200`): same clean result,
  `tomcat_connections_non_binding=True`, peak **1278/3200**.

**Neither run is promoted to canonical Screening** (per this Unit's explicit scope) — a fresh R=160
canonical point will be measured in the new epoch.

## 12. M2 R160 Tomcat connection mean/p95/peak

`m2-r160-lifecyclefix1` (50000 probe, 187 samples): mean **1207.5**, peak **1276**. (p95 not computed
by the collector's `range_query_peak` helper, which reports peak/mean only — not fabricated here;
peak and mean together already fully characterize the non-binding, tightly-distributed population.)
`m2-r160-finalvalue1` (3200 final): mean **1206.9**, peak **1278** — consistent between runs.

## 13. dial timeout count

**0** in both R=160 regressions (was 2680 at the Unit 7.3 `maxConnections=9600` regression, before
this fix).

## 14. EADDRNOTAVAIL count

**0** in both R=160 regressions (was 1129 at the Unit 7.3 `maxConnections=50000` probe, before this
fix).

## 15. Corrected client ESTABLISHED/SYN_SENT/TIME_WAIT

Socket sampler fixed to be endpoint-aware (Section 18 of the governing instruction;
`scripts/sample-client-socket-telemetry.sh`): client-side counts now require local port ≠ target AND
foreign port == target; server-side accepted-socket counts are reported separately
(`server_established`/`server_time_wait`), never summed with the client figures. At R=160 post-fix
(`m2-r160-lifecyclefix1`): client `established` peak 1275 ≈ `server_established` peak 1275 (correct
1:1, confirms the fix), `syn_sent` peak 1 (negligible), `time_wait` (client) peak 5060 vs
`server_time_wait` peak 1 (client is now the active closer, as expected). No validity threshold
added, per instruction — recording/diagnostic only.

## 16. VU peak

`m2-r160-lifecyclefix1`: `vus.max=1273`. `m2-r160-finalvalue1`: `vus.max=1277`. Both closely tracking
the organic Tomcat connection peak (1276/1278) now that connections are properly released — a
near-1:1 VU-to-connection relationship post-fix, in sharp contrast to Unit 7.4's pre-fix finding of
~8.25 connections per VU.

## 17. Organic connection residency interpretation

Post-fix, per-connection residency for `maxConnections` accounting purposes is dominated by the
active-stream duration alone (~8s nominal), not stream+keep-alive — the keep-alive-driven inflation
identified in Units 7.3-7.4 is resolved. This is inferred from the close correspondence between the
organic peak (~1276-1278) and Little's Law on stream duration alone (~1280), not independently
re-instrumented at the Tomcat-internals level (Tomcat exposes no idle-vs-active connection-state
breakdown).

## 18. Final Tomcat maxConnections formula

```
OPEN_TOMCAT_MAX_CONNECTIONS = ceil(SAFE_OPEN_MAX(256) * STREAM_DURATION_SLO_S(10.0) * 1.25) = 3200
```

Same shape as M3's existing `OPEN_M3_MAX_CONNECTIONS` formula, now converging to the same value —
the keep-alive term from Unit 7.3's formula is dropped as no longer necessary. Not extrapolated from
a single probe's peak; derived from the protocol-level understanding validated in §17, then confirmed
non-binding at the computed value (§11-12).

## 19. Final M1/M2 maxConnections

**3200**, applied identically to both (`scripts/run-phase4-open-benchmark.sh`,
`SERVER_TOMCAT_MAX_CONNECTIONS`).

## 20. maxThreads/acceptCount unchanged 여부

**Unchanged**, confirmed still non-binding: `tomcat_threads_busy_threads` peak observed at just 1-2
in both R=160 regressions (vs. `maxThreads=200`). No independent evidence for `acceptCount` binding
either (Unit 7.4's finding that `SYN_SENT` collapsed once the true connection-count issue was
addressed applies with even more force now that the fix, not just a larger ceiling, removed the
pressure).

## 21. M3 smoke

`m3-r2-lifecyclefix1`: clean, GREEN, `dropped=0`, `completion_ratio=1.0`. No R=160 run performed for
M3 (unaffected by the Tomcat/client-lifecycle changes; not required by the governing instruction).

## 22. M3 Open pool config

Unchanged: `OPEN_M3_MAX_CONNECTIONS=3200` / `OPEN_M3_PENDING_ACQUIRE_MAX_COUNT=3200`. Not re-tuned.
(Coincidentally now numerically identical to M1/M2's new value, §18 — not a forced match, both
converge independently from the same `SAFE_OPEN_MAX × STREAM_DURATION_SLO × 1.25` formula shape.)

## 23. VU formula status

**Unchanged from Unit 7.3, confirmed still valid**: `PRE_ALLOCATED_VUS=min(RATE*60,20000)`,
`MAX_VUS=min(PRE_ALLOCATED_VUS*1.25,25000)`. No dropped_iterations issues in any regression this Unit.

## 24. dropped validity status

**Unchanged, confirmed held throughout**: `dropped_iterations==0` required unconditionally
(`scripts/collect_phase4_open_result.py`). Zero dropped iterations in every regression run this Unit
(M1 R2, R10; M2 R4, R160×2; M3 R2; Direct-Mock R2, R160).

## 25. driver STOP semantic status

Unchanged from Unit 7.1/7.3: `UNEXPLAINED` classifications trigger a full-driver stop in both
`initial_pass()` and `refine()`; `tomcat_connections_non_binding` is in `NO_RETRY_KEYS`. Synthetic
tests re-run this Unit: **still ALL PASS (5/5)**.

## 26. collector semantic status

`scripts/collect_phase4_open_result.py`: `dropped_eq_0` (unconditional) and
`tomcat_connections_non_binding` (Unit 7.3) both confirmed working correctly across every regression
run this Unit (imported/executed without error, correct validity verdicts observed in every run's
`result.json`/`validity.json`).

## 27. socket telemetry correction

Applied and verified (§15) — `scripts/sample-client-socket-telemetry.sh` now splits client vs.
server-side rows by comparing local/foreign address ports rather than matching the target port in
either column. Verified against live `netstat` output before use and cross-checked against Tomcat's
own connection gauge in every R=160 regression run (near-exact 1:1 correspondence, confirming the fix
is correct, not just plausible).

## 28. synthetic/regression test count

2 ad hoc lifecycle synthetics (§4) + 5 driver synthetic tests (unchanged, re-run) + **8 real
regression/smoke harness runs**: `direct-mock-r2-lowrate1`, `direct-mock-r160-lifecyclefix1`,
`m1-r2-lifecyclefix1`, `m1-r10-lifecyclefix1`, `m2-r4-lifecyclefix1`, `m2-r160-lifecyclefix1`,
`m2-r160-finalvalue1`, `m3-r2-lifecyclefix1`. All clean/PASS per their respective criteria.

## 29. Open FINAL harness freeze PASS/FAIL

**PASS.** Every §14 (Unit 7.3)/§27 (governing instruction) STOP condition was checked and none
triggered: no premature stream truncation, no event-count regression, Direct-Mock high-rate clean, no
dial-timeout recurrence, no EADDRNOTAVAIL recurrence, Tomcat connections non-binding at the final
value, no dropped-iterations recurrence, clock integrity normal in every checked run, no
client/server population divergence, postflight clean in every run.

**# OPEN SCREENING FINAL FREEZE** is declared for: `scripts/run-phase4-open-benchmark.sh`,
`scripts/collect_phase4_open_result.py`, `scripts/run_phase4_open_screening.py`,
`load-test-k6/scenarios/05-phase4-open-arrival-rate.js`. No further semantic changes to these files
during canonical Screening.

## 30. Logical epoch name

**`open-final-client-lifecycle-v1`**

## 31. R2 full restart recommendation

**Recommendation stands, now to be acted on in the new epoch**: M1/M2/M3 all fresh-screened from
R=2 under `open-final-client-lifecycle-v1`'s frozen semantics. Every existing partial Open run
(Unit 7's original screening, Unit 7.1-7.5's diagnostics/regressions/probes) is preserved as
history/comparison evidence only, never auto-promoted.

## 32. Open Screening resume readiness

**PASS.** All blocking issues from Units 7.1-7.4 are resolved and regression-validated:
connection-lifecycle root cause fixed and confirmed (§7, §11-14), Tomcat headroom finalized and
validated non-binding (§18-20), VU sizing confirmed still valid (§23), dropped-iterations validity
confirmed held (§24), driver STOP semantics confirmed intact (§25), socket telemetry corrected
(§27). Canonical Open Screening under epoch `open-final-client-lifecycle-v1` may begin — **not
started in this Unit**, per its explicit scope (fix + regression + freeze only).
