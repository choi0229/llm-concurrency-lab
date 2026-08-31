# Phase 4 Open Unit 7.1 — M2 R=160 Front-door Failure Forensics

Offline forensic investigation only. **No new load was run** (no M2 R=160 rerun, no M3 R=160 rerun,
no higher rate, no Open Formal). All data below comes from artifacts that already existed before
this Unit started. See sibling docs for full detail: `population-accounting.md`,
`k6-transport-errors.md`, `tomcat-metrics-audit.md`, `async-lifecycle-audit.md`, `backlog-math.md`,
`driver-stop-regression.md`.

## 1. R=2~80 offline integrity audit (read-only, no re-run)

All six existing M2 points below were re-read and cross-checked (client cohort vs. Gateway
`gateway_requests_total`/`http_server_requests_seconds_count` vs. Mock
`mockllm_completed_requests_total`), not just their pre-computed `result.json` color:

| R | client actual_started | client completed | failed_no_event | dropped | Gateway completed | Mock completed | Match? |
|---|---|---|---|---|---|---|---|
| 2 | 236 | 236 | 0 | 0 | 360 | 360 | exact |
| 5 | 601 | 601 | 0 | 0 | 901 | 901 | exact |
| 10 | 1201 | 1201 | 0 | 0 | 1801 | 1801 | exact |
| 20 | 2400 | 2400 | 0 | 0 | 3600 | 3600 | exact |
| 40 | 4799 | 4799 | 0 | 0 | 7200 | 7200 | exact |
| 80 | 9601 | 9601 | 0 | 0 | 13045 | 13045 | exact |

Every point: `completion_ratio=1.000`, zero `failed_no_event`/`failed_mid_stream`/`dropped`, backlog
non-rising, Gateway/Mock counts agree exactly (the Gateway/Mock figures being higher than the
client's measurement-only count is expected — they're whole-process totals including the 60s warmup
phase). **No gap of any kind exists at R≤80.** Canonical GREEN is maintained for all six points; none
were or need to be re-run.

## 2. R=160 population accounting

See `population-accounting.md` for the full stage table. Headline: k6 believes it made **28800**
total HTTP/SSE requests (`iterations.count = http_reqs.count = 28800`) over the whole process
lifetime, but **every Gateway-side counter — Tomcat connector (`tomcat_global_request_seconds_count`,
after subtracting actuator/healthz traffic), servlet dispatch (`gateway_requests_started_total`),
app-level virtual-thread dispatch (`virtual_tasks_started_total`), Mock receipt
(`mockllm_first_chunk_latency_seconds_count`), Mock completion (`mockllm_completed_requests_total`),
and Gateway terminal-outcome (`gateway_requests_total`) — agree exactly at **23363**, with zero
variance across five independently-maintained counters in two separate processes. The ~5437-request
gap exists entirely **before** Tomcat's own connector accounting; nothing inside the Gateway JVM ever
sees a different number.

## 3. k6 transport error result

Zero. Grepped the complete `k6-stdout.log` (617 lines) for `sse error` (the scenario's own
`client.on('error')` log line), `level=error`, `level=warning`, `ERRO[`, `WARN[` — no matches
anywhere. See `k6-transport-errors.md`.

## 4. Actual VU usage

`vus.max = 1788` (peak), against `PRE_ALLOCATED_VUS=3000` / `MAX_VUS=8000`. `dropped_iterations = 0`.
The earlier VU-sizing bug (fixed pre-Screening) is confirmed not to be a factor in this run — k6's own
VU pool was never close to exhausted.

## 5. Tomcat effective config

Confirmed from **runtime metrics, not documentation memory**: `maxConnections=8192`
(`tomcat_connections_config_max_connections`), `maxThreads=200`
(`tomcat_threads_config_max_threads`). Neither is overridden anywhere in `application.yml` or Java
source (confirmed by grep — no customizer bean exists). `acceptCount` is not exposed by Micrometer's
TomcatMetrics binder and could not be read from runtime metrics either way.

## 6. Tomcat busy/current metric evidence

At final scrape: `tomcat_connections_current_connections=244` (97% below the 8192 ceiling),
`tomcat_threads_current_threads=200` (pool had grown to max at some point) but
`tomcat_threads_busy_threads=1` at that instant, `tomcat_global_error_total=0`. Average time spent
per request in the servlet/dispatcher layer: **~96 microseconds** (`tomcat_servlet_request_seconds_
sum=2.266s` / `count=23553`). Gateway process CPU averaged **0.37 of 10 cores** for the whole run.
See `tomcat-metrics-audit.md`.

## 7. AsyncContext / request-thread lifetime

Confirmed from source (`ChatController.stream()`): `request.startAsync()` is called essentially at
the top of the method; the Tomcat connector thread is released back to its pool once the method
returns (a handful of in-process statements, no blocking I/O) — it is **not** held for the request's
7–28s streaming lifetime. That work happens entirely on a virtual thread spawned by
`VirtualTaskSubmitter`. Directly corroborated by the ~96µs/request servlet-layer average in #6.
Treating `160 req/s × ~8s ≈ 1280` as a *Tomcat thread* requirement is a category error — that figure
describes virtual-thread population, a pool M2 deliberately leaves unbounded, not the 200-thread
connector pool. See `async-lifecycle-audit.md`.

## 8. Connector ceiling evidence

**No.** None of `tomcat_threads_busy_threads` reaching 200, `tomcat_connections_current_connections`
reaching 8192, any `tomcat_global_error_total` increment, or any client-observed transport error
exists in this artifact set. Combined with #2's finding that Tomcat's own connector counter matches
the app-dispatch count exactly (no gap *at* the connector), the Tomcat-connector-limiter hypothesis
(the leading hypothesis reported before this Unit started) is **ruled out** by direct evidence, not
merely unconfirmed.

## 9. OS/TCP evidence

Gateway-side: FD peak 9647 against a max of 1,048,576 — nowhere near exhaustion; RSS/CPU show no
resource pressure. **Client (k6 process) OS/socket telemetry does not exist** — the harness only
samples the Gateway process (RSS/FD/CPU), never k6's own. This is a genuine, disclosed evidence gap:
a client-local condition (e.g. ephemeral port/TIME_WAIT pressure on the loopback interface at ~160
new long-lived connections/sec) cannot be confirmed or ruled out from artifacts on hand.

## 10. Measurement-end active backlog

The harness's own drain loop (`run-phase4-open-benchmark.sh`) polls `gateway_active_requests`
immediately after k6 exits and found **0** on its very first check (`drain_start_ms` to
`drain_end_ms` = 24ms total — it never needed a second poll). Mid-run, backlog
(`virtual_tasks_active`/`gateway_active_requests`) sat at 862–992 concurrently; by the instant k6's
process ended, Gateway had zero outstanding work. Nothing was "still draining" at cutoff — whatever
was in flight had already finished or never existed by that point.

## 11. 34.4 req/s deficit × 120s vs. 4127 comparison — corrected per review

**Not root-cause evidence.** `actual_started(19199) − completed(15072) = failed_no_event(4127)` is a
direct consequence of the cohort-accounting identity `actual_started ≈ completed + failed_no_event`
(true here since `rejected=0`, `failed_mid_stream=0`). `34.4 req/s × 120s ≈ 4128` is the same
statement rearranged, not an independent measurement — retained only as a consistency check on the
cohort counters, not as evidence for a backlog-driven mechanism. The backlog *trend itself* (falling
992→863 across the window, not rising) argues against a "continuously worsening, still-saturated-at-
cutoff" reading. The previously-proposed "warmup 60s too short to fill R=160's ~1280-concurrency
steady state" explanation is **withdrawn** — that misapplied Little's Law (1280 is a concurrency
count, not a time value; the fill-time constant is ~service-time ≈8s, and warmup=60s is ~7.5× that,
generally sufficient). See `backlog-math.md` for the corrected reasoning. The falling backlog trend
remains unexplained and is carried into Unit 7.2 as an open question, not attributed to any cause
here.

## 12. Gateway/Mock completion accounting

23363 = 23363 = 23363 = 23363 = 23363 across `gateway_requests_started_total`,
`virtual_tasks_started_total`, `mockllm_first_chunk_latency_seconds_count`,
`mockllm_completed_requests_total`, and `gateway_requests_total{outcome="completed"}` (every other
outcome label reads exactly 0.0 for the whole process). 100% of what the Gateway ever dispatched,
succeeded.

## 13. Case A/B/C/D verdict

- **Case A (Tomcat connector/config artificial limiter): RULED OUT.** #2, #6, #7, #8 above are
  direct, mutually corroborating evidence against it — the connector's own counter has zero gap
  relative to app dispatch, thread occupancy per request is microseconds, and no ceiling was
  approached on any Tomcat metric.
- **Case B (genuine Open sustainability/backlog boundary): partially supported, not clean.** The
  throughput deficit (125.6 < 160) is real, but per the correction in #11 the 4127≈4128 arithmetic
  match is not independent evidence for it — it is a restatement of the same cohort identity. The
  backlog trend is *falling*, not rising, across the measurement window (`backlog-math.md`), which is
  atypical for a boundary the system is stably pinned against for the full window. The previously
  proposed "warmup too short" explanation for this is withdrawn (misapplied Little's Law); the
  falling trend remains unexplained.
- **Case C (LoadGen/client transport limit): best fit for the specific 5437-request population gap**
  (#2, #3, #4), but **not provable from offline artifacts** — no client-side OS/socket evidence
  exists (#9) to confirm the exact mechanism (most plausible candidate: a `k6/x/sse`
  extension/loopback-connection-establishment condition that neither errors nor is counted, occurring
  before the server ever sees the attempt).
- **Case D (insufficient evidence): applies to the *specific mechanism* of the population gap**, even
  though Case A can be confidently ruled out and Case B/C can be weighted relative to each other.

**Overall reading: this is not a clean single-case verdict.** The Gateway/Tomcat/JVM layer is
definitively exonerated (Case A ruled out, zero resource pressure anywhere). The genuine throughput
deficit at R=160 is real and reproducible arithmetic, but whether it reflects M2's true sustained
capacity ceiling (Case B) versus a client-side artifact inflating the apparent shortfall (Case C) is
not resolved by the artifacts on hand, and the falling backlog trend leans against a clean Case B
reading. This requires a decision, not further unilateral inference — see §17 options below for how
to proceed.

## 14. M2 R=160 current canonical status

Left exactly as recorded: `valid=true, color=RED, classification=UNCLASSIFIED_MODEL_FAILURE`, at
`docs/test-results/phase4/unit7-open-screening/m2-r160-screen1/`. Not modified, not re-run, not
reclassified. Per the verdict above, this classification is **not** disproven — the "unexplained"
label remains accurate given Case D applies to the core mechanism — but Case A (the most likely
alternative explanation floated before this Unit) is now ruled out with high confidence.

## 15. R=2~80 re-run necessity

**Not needed.** §1's audit found all six points internally and cross-process consistent with
`completion_ratio=1.000` and zero anomalies of any kind. Canonical GREEN stands.

## 16. Tomcat config amendment necessity

**Not needed, and not done.** Case A is ruled out, so there is no config artifact to fix. No changes
were made to `application.yml`, Tomcat connector settings, or any Gateway source file in this Unit.

## 17. Driver STOP bug fix

Fixed in `scripts/run_phase4_open_screening.py`: added the `UNEXPLAINED` set (mirroring the Closed
driver's exactly), a full-driver `trigger_stop()` on any `UNEXPLAINED` classification in both
`initial_pass()` and `refine()` (closing a gap that exists even in the Closed driver's own
`refine_bracket()`), and a per-iteration `STOP` check in `main()`'s refinement loop. Full detail and
rationale in `driver-stop-regression.md`.

## 18. Synthetic tests

`scripts/test_phase4_open_screening_driver_synthetic.py` (new), 5 scenarios, **all PASS**:
(1) the real M2-R160→M3-R160 incident replayed, asserting zero subsequent calls at the same or any
later rate level; (2) `KNOWN_RED` still only deactivates the one model, others continue to R=256;
(3) a second `UNEXPLAINED` classification also halts everything; (4) existing no-retry-INVALID
behavior unaffected; (5) the same stop fires correctly from inside the refinement phase.

## 19. M3 interrupted artifact handling

`docs/test-results/phase4/unit7-open-harness/m3-r160-screen1/` (k6 completed, but several `prom-
*.json` are 0 bytes and no `result.json` exists — postflight was killed mid-sequence along with the
driver). Left in place, untouched, with a new `INTERRUPTED_INCOMPLETE.txt` marker added alongside it
documenting why it must not be used. It was never moved to `unit7-open-screening/` and is therefore
already excluded from `resume_from_existing()`'s scan by construction.

## 20. Open Screening resume readiness

**FAIL — not ready to resume automatically.** The driver bug is fixed and verified (§17/§18), and
R≤80 is confirmed clean (§15), but §13's verdict is not a clean single-case resolution: Case A is
ruled out, but the Case B vs. Case C question for M2 R=160 itself is unresolved, and that
determines (a) whether M2's screening should be considered *complete* at R=160 as a boundary, or
needs further work (e.g. a client-instrumented rerun with k6-process-side OS telemetry captured, or a
longer-warmup rerun to test the transient hypothesis in §11/§13), and (b) how `UNCLASSIFIED_MODEL_
FAILURE` should map onto the frozen Open GREEN/AMBER/RED taxonomy going forward if this turns out to
recur. Per this Unit's explicit scope (§19 of the governing instruction), no such rerun was performed
and none should be until this is decided. Reporting back for that decision now, per §21 of the
governing instruction: **no new load, no Open Formal, stop here.**
