# M2 R=256 two-leg diagnostic — result

Run dir: `m2-r256-two-leg/` · label `diag1` · scenario `06-phase4-open-diagnostic-r160.js` ·
k6 → Gateway `127.0.0.1:18102/chat/stream` → Mock `127.0.0.1:8000/mock/stream`.
M2 production source unchanged · `SERVER_TOMCAT_MAX_CONNECTIONS=3200` · `CHAT_TOTAL_TIMEOUT_MS=60000`
· same workload / warmup 60 / measure 120 / Unit 7.5 `client.close()` / final VU formula (15360 /
19200). `PID_MATCH=true` (gateway-phase4-virtual-thread-0.1.0.jar, JDK 21.0.11+10). One run, no retry.

## The failure reproduced

| | canonical `m2-r256-screen1` | this diagnostic |
|---|---|---|
| measurement started | 30721 | 30719 |
| completed | 25033 | 24440 |
| **failed_no_event (zero-event terminal)** | **5688** | **6279** |
| completion ratio | 0.815 | 0.796 |
| `client_sse_error_total` | 0 | 0 |
| VT started (final) | 38472 | 37865 |
| Mock completed (final) | 38463 | 37849 |
| **VT started − Mock completed** | **9** | **16** |
| **Gateway BindException (`EADDRNOTAVAIL`)** | **9** | **16** |
| virtual_tasks_active peak | 2090 | 2341 |
| tomcat_connections_current peak | 2092 / 3200 | 2343 / 3200 |
| process_cpu_usage peak | 0.09 | 0.137 |

Same signature, marginally larger magnitude (the 2-leg socket sampler + k6-rss sampler add a little
host pressure). **The diagnostic instrumentation revealed the mechanism the canonical run could not
see.**

## Client counters (scenario 06) — the LEG-A deficit is now measured directly

| counter | whole run | measurement cohort |
|---|---|---|
| client_sse_open_attempt_total | **46080** | ≈30719 |
| client_sse_open_success_total | **37865** | ≈24440 |
| client_first_event_total | 37849 | — |
| client_completed_total | — | 24440 |
| client_zero_event_terminal_total | — | **6279** |
| client_sse_error_total | **0** | 0 |
| dropped_iterations | 0 | — |

**open_attempt − open_success = 46080 − 37865 = 8215 iterations that tried to open the SSE stream to
the Gateway and never connected** (measurement-cohort share: 6279). `client.on('error')` never fired
for any of them — the xk6-sse setup callback never ran, exactly the Unit 7.2/7.4/canonical
connect-stage-failure signature, now with the attempt-vs-success counter making it explicit.
`http_req_duration.min = 0.76 ms` — near-instant failed requests present.

## Two-leg socket telemetry (1 Hz, endpoint-aware, k6 PID lifetime; 169 samples)

| | LEG A (k6 → :18102) | LEG B (Gateway → :8000) |
|---|---|---|
| unique local source ports — peak | **8282** | **8120** |
| ESTABLISHED — peak | 2342 | 2342 |
| TIME_WAIT — peak | **7824** | **7616** |
| SYN_SENT — peak | 1 | 0 |
| CLOSE_WAIT / LAST_ACK / CLOSING | 0 | 0 |
| FIN_WAIT_1 / FIN_WAIT_2 — peak | 15 / 3 | 5 / 27 |
| raw rows over run | 1,208,173 | 1,186,395 |

Both legs near-symmetric, both TIME_WAIT-dominated (run totals: 1.85M TIME_WAIT vs 0.55M ESTABLISHED
row-observations). On LEG A k6 is the active closer (Unit 7.5 `client.close()`); on LEG B the Gateway
is the active closer (`BlockingMockLlmRelay`'s unconditional `connection.disconnect()` — see
`jdk-httpurlconnection-audit.md`).

### Combined ephemeral-port occupancy — the pool is exhausted

| set | peak | of 16384-port pool |
|---|---|---|
| **A ∪ B** unique local ports | **16362** | **99.87%** |
| **A ∩ B** unique local ports | **1** | — |

`INTERSECT ≈ 1`: macOS does **not** reuse a local source port toward `:18102` also toward `:8000`
— the two legs' ephemeral ports are effectively **disjoint, so their demands add**. LEG A (~8282) +
LEG B (~8120) ≈ 16362 ≈ the entire 16384 pool. **Per §9 this UNION figure is reported as an
observation, and here it is corroborated as genuine exhaustion by the time-correlated
`EADDRNOTAVAIL` failures below — not asserted from the count alone.**

`lsof` ownership spot-check (5 s): k6 owns 0–2299 ESTABLISHED sockets to `:18102` and **zero** to
`:8000`; the Gateway JVM owns 0–2295 ESTABLISHED sockets to `:8000`. Leg attribution confirmed:
LEG A sockets are k6's, LEG B sockets are the Gateway's, held by two separate processes.

## Time-series correlation (§19) — near-perfect

**All 16** Gateway `BindException` timestamps fall in a second where `A ∪ B` unique ports sit at or
within a few ports of the 16384 ceiling:

| BindException (HH:MM:SS) | A∪B that second |
|---|---|
| 15:11:24, :25 | 16108 → 16357 |
| 15:12:02, :03, :04 | 16359 / 16100 / ~16315 |
| 15:12:43, :47, :49, :53 | 16274 / 16030 / 16278 / 16361 |
| 15:13:17, :19, :20, :24, :26, :28, :32 | 16362 / 16362 / 16362 / 16007 / 16203 / 16362 / 16266 |

When `A ∪ B` pins the ceiling, ESTABLISHED on **both** legs collapses (e.g. 15:11:27 LEG A
est 623; 15:12:03 est 546; 15:13:55 est 1) while TIME_WAIT stays ~7000+ — the textbook "every free
ephemeral port is in TIME_WAIT, new `connect()` returns `EADDRNOTAVAIL`" pattern. New connects then
fail: silently on LEG A (k6 SSE-open → 6279 zero-event, VUs freed → the est collapse), and as the 16
`BindException`s on LEG B. TIME_WAITs age out (2·MSL = 30s), ESTABLISHED recovers, load resumes — a
sawtooth over the whole measurement window.

## Gateway execution state at failure — every internal resource non-binding

| Prometheus series | value during run |
|---|---|
| `virtual_tasks_active` | peak **2341**, mean 1643, **first-half 1723 → second-half 1563 (declining, not rising)** |
| `gateway_active_requests` | == `virtual_tasks_active` (no queue) |
| `process_cpu_usage` | peak **0.137 / 1.0** → ≈ 1.4 of 10 cores; mean 0.05 |
| `jvm_threads_live_threads` | **123–130**, flat (no carrier-thread growth) |
| `tomcat_connections_current_connections` | peak **2343 / 3200** → non-binding |
| `process_files_open_files` | peak 4698 / 1 048 576 → non-binding |
| Gateway RSS (ps) | peak 1.39 GiB |
| Gateway %cpu (ps) | peak 144 (1.4 cores), mean 43 (0.43 cores) |

**Case D (Virtual-Thread execution / scheduler boundary) is decisively excluded:** at the failure
the VT executor backlog is *declining*, CPU is ~1.4 of 10 cores, threads are flat, there is no
queue, and the Tomcat connector is non-binding. Nothing inside the Gateway is saturated. The failure
is 100% at OS ephemeral-port allocation on the two same-host TCP legs.

## Clock integrity — clean this run

`client_ttfc_completed` med 1.00s / p95 1.19s / max 1.64s; `client_completed_stream_duration` med
8.26s / p95 9.09s / max 9.37s; `iteration_duration` med 8.12s / p95 8.99s. Custom JS-timed and k6
built-in network-timed spans **agree** (unlike the Direct Mock run) — so the 6279 zero-event
failures are genuine LEG-A connect failures, not a k6-scheduler timing artifact. k6 rss peak
3.57 GiB, %cpu peak 673 (6.7 of 10 cores), mean 52.

## Sampler perturbation check (§5)

Two-leg sampler self-measured **6.66% of one core** over its ~193 s lifetime (12.9 CPU-s);
one 2 s `ps` window caught 40.9% (a netstat parse spike over ~16k rows), mean 7.2%. lsof sampler
mean 0.4%. Aggregate < 0.5 core on a 10-core host also running k6 (6.7 cores) and Gateway (1.4
cores). The observed effect is a **structural resource ceiling** (pool size is fixed at 16384; the
two legs' disjoint demand sums past it), not a timing-sensitive effect a few % CPU could create or
mask. **No perturbation that changes the verdict.**

## Verdict — Case B

**SHARED-HOST TRANSPORT RESOURCE CONFOUND.** Matches §20 point by point:

- Direct Mock R=256 transport-clean (single-leg, no `EADDRNOTAVAIL`) — Case A ruled out.
- LEG A in isolation is drivable at R=256 (Direct Mock: ~10324 ports, ~6000 free).
- **Only in the M2 two-leg run**: LEG A open-failure appears (6279), LEG B carries its own
  TIME_WAIT / source-port pressure (~7600 TW, ~8120 ports), the two legs' **combined** unique-port
  demand reaches 16362 / 16384 with essentially no port sharing (∩ = 1), the Gateway raises 16
  outbound `BindException`s, and every failure second coincides with the pool ceiling.
- VT / CPU / carrier threads / Tomcat connector / FD all non-binding.

This is a **benchmark-control confound**: the LoadGen (k6) and the SUT (Gateway) run on one host and
draw from the same 16384-entry ephemeral-port pool; at R=256 their combined outbound connection
churn exhausts it. It is **not** an M2 architecture RED, and it is **not** a Virtual-Thread finding.
Per §20, `m2-r256-screen1` must **not** be promoted to architecture RED.

The Gateway→Mock leg's own churn (source-confirmed `disconnect()`-per-request, ~one active close per
request → ~7600 TIME_WAIT) is a real contributor to the pressure — but LEG B alone (~8120 ports)
fits the pool, and LEG A alone (~10324 ports) fits the pool; only the **sum on one host** breaks.
That is the definition of Case B, not Case C.
