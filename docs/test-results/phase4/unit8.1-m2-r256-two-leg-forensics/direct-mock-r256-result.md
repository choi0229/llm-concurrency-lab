# Direct Mock R=256 diagnostic — result & §10 gate

Run dir: `direct-mock-r256/` · label `diag1` · scenario `06-phase4-open-diagnostic-r160.js`
k6 → Mock `127.0.0.1:8000/mock/stream` direct (no Gateway) · warmup 60s / measure 120s ·
firstChunkDelay 1000 / chunkInterval 200 / chunkCount 35 / chunkSize 64 · Unit 7.5 `client.close()`
lifecycle · final VU formula (PRE_ALLOCATED_VUS 15360, MAX_VUS 19200) · one run, no retry.

## Client counters (scenario 06)

| counter | whole run | measurement cohort |
|---|---|---|
| iterations / http_reqs | 46080 | — |
| client_iterations_started_total | 46080 | — |
| client_sse_open_attempt_total | 46080 | — |
| client_sse_open_success_total | **46080** | — |
| client_first_event_total | **46080** | — |
| measurement_iterations_started_total | — | **30716** |
| client_completed_total | — | **30716** |
| client_failed_no_event_total / client_zero_event_terminal_total | — | **0** |
| client_failed_mid_stream_total | — | 0 |
| client_sse_error_total | 0 | 0 |
| client_rejected_total | 0 | 0 |
| dropped_iterations | **0** | — |

`open_attempt == open_success == first_event == 46080` — **every iteration established its SSE
stream and received ≥1 event. Zero connect-stage loss.**

## Mock population

`mockllm_completed_requests_total = 46080` · `cancelled = 0` · `failed = {}` ·
`active/waiting/current_concurrency` all 0 at end. **Mock requested == completed == k6 open-success.
Full accounting, no deficit.**

## Client-side socket telemetry — DIRECT leg (k6 ephemeral → :8000)

| quantity | peak |
|---|---|
| unique local source ports | **10324** |
| ESTABLISHED | 2498 |
| TIME_WAIT | 8181 |
| SYN_SENT | **1** (one lsof spot-check caught a transient 28) |
| CLOSE_WAIT / LAST_ACK / CLOSING | 0 |
| FIN_WAIT_2 | 52 |

Ephemeral range 49152–65535 = **16384 ports**. Peak single-leg occupancy **10324 / 16384 ≈ 63%**,
leaving ~6060 ports free → **no exhaustion, no `EADDRNOTAVAIL`, no dial/connect timeout**. Steady
state ~2200 ESTABLISHED + ~7700 TIME_WAIT (k6 is the active closer per iteration via the Unit 7.5
`client.close()`; TW ≈ 256/s × 2·MSL(30s) ≈ 7680 — matches). `lsof` confirms the k6 process owns all
~2200–2450 k6→Mock ESTABLISHED sockets and owns **zero** k6→:18102 sockets.

## §10 PASS gate

| criterion | result |
|---|---|
| k6 exit 0 | ✓ |
| dropped = 0 | ✓ |
| actual arrival on target | ✓ 30716 / 30720 = 99.99% |
| open_attempt ≈ open_success | ✓ 46080 == 46080 |
| zero-event failure = 0 | ✓ 0 |
| mid-stream = 0 | ✓ 0 |
| sse_error = 0 | ✓ 0 |
| Mock requested/completed population consistent | ✓ 46080 / 46080, cancelled 0 |
| no `EADDRNOTAVAIL` | ✓ 0 |
| no dial/connect timeout | ✓ 0 (no zero-event, no error callback) |
| SYN_SENT abnormal accumulation | ✓ peak 1 |
| clock integrity | ✓ system clock sane — `client_ttfc` min 0.998s (= exact 1000ms firstChunkDelay), `iteration_duration` min 7849ms (= nominal) |
| postflight clean | ✓ OK |

### → GATE = PASS

Per §12 the conclusion is strictly: **the single-leg k6→Mock R=256 path is transport-clean** — no
ephemeral-port exhaustion, no connect backlog, full population accounting. **Case A (loadgen / client
/ host control limit) is ruled out.** This does **not** establish that the same-host two-leg M2 run is
control-safe; that is what the M2 two-leg diagnostic tests.

## Caveats recorded (weighed in the B/C verdict — NOT gate-fail conditions per §10)

1. **k6-side latency tail.** `client_completed_stream_duration` p90 = 42.9s / p95 = 45.2s (median
   fine, 8.5s), `client_ttfc` max = 37.9s, while k6's own `iteration_duration` p95 = 9.1s / max
   9.9s. The custom JS-timed span diverges from the network-timed span by ~5×. Every stream still
   completed with `final` (Mock cancelled = 0). This is k6's JS runtime / goroutine scheduler being
   starved at `vus.max = 2495` on a fully-successful R=256 load — a loadgen saturation artifact, not
   a transport or Mock failure. (In the M2 run k6 was *less* saturated — ~20% of iterations fail
   fast and free VUs — and there the custom/built-in spans agree, so the M2 failed population is
   genuinely transport, not this artifact.)
2. **Single leg already at 63% of the host ephemeral pool** (10324 / 16384). Directly implies the
   two-leg M2 run's combined demand will approach the ceiling — confirmed in
   `m2-r256-diagnostic-result.md`.
3. Sampler overhead: two-leg sampler self-measured **2.53% of one core** over 300s; no perturbation.
