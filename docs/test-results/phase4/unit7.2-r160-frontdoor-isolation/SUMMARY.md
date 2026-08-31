# Phase 4 Open Unit 7.2 — R=160 Transport/Front-door Isolation Diagnostic

Diagnostic-only. Neither `direct-mock-r160-diag1` nor `m2-r160-diag1` is a canonical Screening or
Formal point. No M3 R=160, no higher rate, no Open Formal was run. Raw artifacts:
`docs/test-results/phase4/unit7.2-r160-frontdoor-isolation/{direct-mock,m2}-r160-diag1/`.

## 1. Direct-Mock R=160 control (Section 8/9)

Same workload, same warmup=60s/measurement=120s, same VU sizing, same host, new scenario
(`06-phase4-open-diagnostic-r160.js`) with added client-side counters, k6 → Mock directly (no
Gateway).

| | value |
|---|---|
| `client_iterations_started_total` | 28801 |
| `client_sse_open_attempt_total` | 28801 |
| `client_sse_open_success_total` | **28801** |
| `client_first_event_total` | **28801** |
| `dropped_iterations` | 0 |
| Mock `received_first_chunk_count` / `completed_total` | 28801 / 28801 |
| `client_zero_event_terminal_total` | **0** |
| socket telemetry (target port 8000) | `syn_sent`: peak **0**, mean 0.0; `established`: peak 4168; `time_wait`: peak 5137; `close_wait`: 0 |

**100% clean.** Every iteration opened, connected, and completed. Zero SYN_SENT accumulation at any
point. Loadgen/host general-capacity hypothesis (broad form of Case C) is weakened: this exact
client, host, workload, and rate sustains cleanly when the only thing removed is the Gateway.

## 2. M2 R=160 diagnostic (Section 11, run once per the Direct-Mock-clean gate)

| | value |
|---|---|
| `client_iterations_started_total` / `client_sse_open_attempt_total` | 28801 / 28801 |
| `client_sse_open_success_total` | **23396** |
| `client_first_event_total` | 23396 (identical to open_success — every successfully-opened connection got at least one event) |
| `client_zero_event_terminal_total` | **4096** |
| `dropped_iterations` | 0 |
| Gateway `gateway_requests_started_total` / `virtual_tasks_started_total` / outcome-sum | 23396 / 23396 / 23396 (all exactly equal) |
| Mock `received_first_chunk_count` / `completed_total` | 23396 / 23396 |
| socket telemetry (target port 18102) | `syn_sent`: **peak 1322**, mean 261; `established`: peak 16584; `time_wait`: peak 3580; `close_wait`: 0 |

## 3. The decisive evidence: actual client error text (Section 5/6)

Every one of the 4096 `zero_event_terminal` diagnostic log lines carries the **same** `res_json`
(not fabricated — this is exactly what `sse.open()`'s return value contained, `JSON.stringify`'d
verbatim):

```json
{"url":"","status":0,"headers":{},"error":"Post \"http://127.0.0.1:18102/chat/stream\": dial tcp 127.0.0.1:18102: connect: operation timed out"}
```

This is a **Go `net/http` TCP dial-timeout** — the client's `connect()` never received a SYN-ACK in
time. It occurs *before* any HTTP request is sent, which is why `sawOpen=false` and `sawError=false`
for every one of these (xk6-sse's `on('open')`/`on('error')` callbacks never fire — the connection
never reached a state where the extension considers a session to exist, exactly as hypothesized,
now confirmed, in Unit 7.1's `k6-transport-errors.md`). xk6-sse's error-object limitation noted in
Unit 7.1 (empty `{}` from `on('error')`) turned out not to matter here — the real signal was in
`sse.open()`'s own return value (`res`), which this diagnostic's added logging captured for the
first time. No error-code taxonomy was invented; this is the literal string the Go HTTP client
produced.

## 4. Tomcat time-series (not just final snapshot) — Section 4, the check Unit 7.1 was missing

`tomcat_connections_current_connections` (raw `prom-tomcat_connections_current_connections.json`,
188 one-second samples): **peaked at exactly 8192.0 — the configured `maxConnections` ceiling
(`tomcat_connections_config_max_connections=8192`, confirmed again this run) — and stayed at or
above 8000 for 126 of 188 samples (~2 minutes, essentially the entire measurement window)**, not a
brief blip. This directly contradicts Unit 7.1's "Case A ruled out" conclusion, which relied only on
the **post-hoc final snapshot** (244, taken after the load had already ended and connections had
drained) — exactly the gap the governing instruction's Section 4 anticipated ("final 값만으로
saturation 기각 금지").

Meanwhile, in the same run: `tomcat_threads_busy_threads` peaked at only **107 of 200**, and Gateway
`process_cpu_usage_mean ≈ 0.033` (~3.3% average). **The thread pool and CPU were never remotely
close to their limits — only the connection-count ceiling was.** This is consistent with (and
refines, rather than contradicts) Unit 7.1's `async-lifecycle-audit.md` finding that Tomcat threads
are held only ~96µs per request — that finding rules out `maxThreads` as the bottleneck, but says
nothing about `maxConnections`, which counts *all* connections the connector is tracking (including
idle keep-alive ones awaiting reuse or Tomcat's `keepAliveTimeout`, default 60s), independent of how
briefly each one occupies a worker thread.

## 5. Exact gap location (Section 12)

| Stage | From → To | Population | Gap |
|---|---|---|---|
| A→B | iteration started → SSE open attempted | 28801 → 28801 | 0 |
| **B→C** | **SSE open attempted → connection established** | **28801 → 23396** | **5405 — 100% of the gap is here** |
| C→D | connection established → Tomcat HTTP request observed (`gateway_requests_started_total`) | 23396 → 23396 | 0 |
| D→E | ChatController entered → VT submit (`virtual_tasks_started_total`) | 23396 → 23396 | 0 |
| E→F | VT submit → Mock received | 23396 → 23396 | 0 |
| F→terminal | Mock received → Mock completed, Gateway outcome=completed | 23396 → 23396 → 23396 | 0 |

The entire population gap sits at exactly one stage: **TCP connection establishment to the Gateway's
listening socket.** Everything after a connection actually completes its handshake proceeds with
zero loss, zero errors, 100% success — matching Unit 7.1's original finding exactly, now with the
missing piece (why some connections never got that far) identified.

## 6. Tomcat binding evidence (Section 13) — meets the "multiple simultaneous evidence" bar

1. `tomcat_connections_current_connections` time series pinned at the exact `maxConnections=8192`
   ceiling for ~2 minutes (not a default-value-exists argument — an actual observed saturation
   event).
2. Client-side `syn_sent` accumulating up to 1322 concurrently, specifically and only against the
   Gateway's port (zero for Direct-Mock at the identical rate/workload/host).
3. The client's own captured error text is a raw TCP-level `connect: operation timed out` — the
   textbook symptom of a listening socket whose accept queue cannot keep up.
4. Zero corresponding evidence of thread-pool or CPU saturation in the same run (rules out
   `maxThreads`/CPU as alternative explanations for the same window).
5. Zero reproduction of any part of this in Direct-Mock at the same rate (rules out generic
   host/loadgen limits).

All five hold simultaneously, in the same run, satisfying the "복수 동시 evidence" bar the governing
instruction set for a Case A verdict (a single default-value observation would not have been
enough — this is five independent, corroborating signals from three different data sources: k6
client diagnostics, Gateway/Tomcat Prometheus metrics, and client-host socket telemetry).

## 7. LoadGen/OS binding evidence (Section 14)

Not the primary finding. Ephemeral port range (16384) was never close to exhausted in either run
(M2 run's peak `established+time_wait+syn_sent` ≈ 16584+3580+1322 — **this does approach/exceed the
16384-port range**, which is a secondary, downstream consequence of the same root cause: connections
piling up waiting on the Gateway's connector rather than a client-side limit that exists
independently of the Gateway). `close_wait=0` throughout (no server-side-close-not-acknowledged
issue). No `EMFILE`/FD-exhaustion evidence (`fd_soft_limit_at_k6_launch=1048576`). The Direct-Mock
control run, at the identical rate, never approached any of these numbers (`syn_sent=0` throughout,
`established` peak only 4168). **LoadGen/OS is not the root cause; the observed socket pressure is
downstream of the Gateway-side connection admission ceiling.**

## 8. App-layer backlog evidence (Section 15)

Once a connection is established, there is zero application-layer loss: `gateway_requests_started_
total = virtual_tasks_started_total = mock_received = mock_completed = gateway_outcome_completed =
23396`, exactly, with zero rejections/timeouts/errors of any kind recorded anywhere above the TCP
layer. Per the governing instruction's explicit framing (§15: "app까지 오지 않은 요청을 M2 architecture
backlog라고 해석하지 않는다"), the requests that never got a TCP connection are **not** attributed to
M2's virtual-thread/backlog architecture — M2's actual per-request handling, once given a chance to
run, is flawless at this rate.

## 9. Case A/B/C/D verdict (Section 16)

# Case A — TOMCAT / GATEWAY FRONT-DOOR CONFIG LIMIT (CONFIRMED)

`maxConnections=8192` (an unexamined Spring Boot/Tomcat default, never tuned for Open-model
sustained-arrival-rate workloads, where connection *churn* — not steady concurrency — is what stresses
this particular setting) is the binding constraint. This is a genuine Gateway/Tomcat configuration
gap specific to the Open load model, not a finding about M2's virtual-thread architecture, not a
loadgen/OS/client limitation, and not an unexplained phenomenon. Case B, C, and D are all superseded
by this direct, multi-source-corroborated evidence.

**Most likely specific mechanism (well-supported by arithmetic, not independently
instrumented beyond what's above):** Tomcat's connector counts a connection toward `maxConnections`
for as long as it exists, including idle time in HTTP keep-alive after a response completes (Tomcat
default `keepAliveTimeout=60000ms`, never customized here). Each SSE stream itself only takes ~8s,
but if the underlying TCP connection then lingers in keep-alive for up to an additional ~60s before
Tomcat's connector reaper closes it, the *effective* per-connection lifetime for `maxConnections`
accounting purposes is closer to ~60-68s, not ~8s. Little's Law with that effective duration —
`160 req/s × ~68s ≈ 10,880` — comfortably exceeds `maxConnections=8192`, which is exactly consistent
with the observed sustained pinning at the ceiling. This refines, rather than replaces, the
population-accounting/backlog work in Unit 7.1 — it explains *why* the ceiling binds at this
specific rate. No `tomcat_*` metric distinguishes "actively streaming" from "idle keep-alive"
connections, so this specific mechanism (as opposed to the ceiling-binding fact itself, which is
directly proven) is not independently confirmed beyond this arithmetic consistency.

## 10. M2 R=160 canonical status (Section 17)

**Still not promoted to canonical.** The `m2-r160-screen1` artifact in `unit7-open-screening/`
remains exactly as recorded (`RED`/`UNCLASSIFIED_MODEL_FAILURE`), unmodified. Per Case A now being
confirmed, that classification is best understood as **CONTROL/CONFIG-caused, not a genuine model
capacity finding** — but per Section 23's Case A path, the correct next step is a Tomcat headroom
amendment design + user approval + regression + a fresh Open epoch, not a unilateral reclassification
of the existing point. No config change has been made.

## 11. R2~80 status (Section 18)

Unchanged from Unit 7.1: all six points clean, canonical GREEN retained, no re-run performed or
needed.

## 12. Open logical epoch recommendation (Section 19)

Given Case A is now confirmed as a genuine Gateway-side config gap affecting at least M2's R=160
point (and plausibly M1, which also runs on Tomcat with the same unexamined defaults — not
investigated in this Unit, out of scope per §18's "M2/control 문제를 완전히 닫는다" ordering), the
existing partial Open Screening state (`docs/test-results/phase4/unit7-open-screening/`) should
**not** be treated as final. Recommended path once the user approves a Tomcat headroom amendment:
(a) design the amendment (raise `maxConnections`/tune `keepAliveTimeout` or `maxKeepAliveRequests`
for the Open-model harness specifically, sized off `SAFE_OPEN_MAX` with the same headroom-policy
precedent used for M3's WebClient pool), (b) regression-test at a low rate to confirm no behavior
change, (c) re-run R=160 (and audit whether R≤80 points are also affected, even though they show no
symptoms — their `established` connection counts are much lower and may simply never have
approached 8192), (d) declare a new Open epoch only after that, with the superseded pre-amendment
artifacts kept as history, not deleted.

## 13. Open Screening resume readiness (Section 20)

**FAIL — do not resume automatic Screening yet.** Root cause is now identified and evidenced (unlike
Unit 7.1's inconclusive state), but no config change has been applied, no regression has been run,
and per the explicit Case A next-step ordering (design → **user approval** → regression → new epoch),
applying the fix is a decision for the user, not something to do unilaterally here.

## Appendix: M1 R=10 canonical artifact status (Section 1, carried over from the review)

Per the dropped_iterations validity semantic revert (`scripts/collect_phase4_open_result.py`,
`dropped_ok = (dropped == 0)` unconditionally, restored): **all three existing M1 R=10 attempts are
now correctly INVALID** — `m1-r10-screen1` (dropped=401), `m1-r10-screen1-retry1` (dropped=401), and
`m1-r10-screen1-postvufix` (dropped=251, previously marked valid only under the now-reverted
`dropped_eq_0_or_reliability_failed` semantic — re-collected in place against its unchanged raw logs;
original result/validity preserved as `result-pre-dropped-semantic-revert.json` /
`validity-pre-dropped-semantic-revert.json` siblings). **M1 currently has no valid canonical R=10
point at all.** A fresh M1 R=10 run is needed once Screening resumes; not performed here (out of this
Unit's scope, and would be "new load" beyond what was authorized).
