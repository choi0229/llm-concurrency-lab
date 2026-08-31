# R=256 population accounting — final (from the Unit 8.1 live diagnostics)

§17 table. Measured values only. `—` = not separately instrumented.

## M2 R=256 two-leg diagnostic (`m2-r256-two-leg/`)

Whole run = warmup + measurement (46080 iterations). Measurement cohort = 30719.

| # | Population | Whole run | Measurement cohort | Source |
|---|---|---|---|---|
| A | k6 iterations started | 46080 | **30719** | `client_iterations_started_total` / `measurement_iterations_started_total` |
| B | SSE open attempted | **46080** | ≈30719 | `client_sse_open_attempt_total` |
| C | SSE open succeeded | **37865** | ≈24440 | `client_sse_open_success_total` |
| D | first SSE event received | 37849 | ≈24440 | `client_first_event_total` |
| — | zero-event terminal (LEG-A connect fail) | ≈8215 | **6279** | `client_zero_event_terminal_total` (measurement); whole = B − C |
| E | Gateway request reached | — | — | (no distinct counter; bounded below by F) |
| F | Gateway VT task started | **37865** | — | `virtual_tasks_started_total` final |
| G | Gateway outbound relay attempted | ≈37865 | — | `gateway_active_requests` peak == `virtual_tasks_active` peak (2341); no queue |
| H | Mock request reached | ≈37865 | — | H ≈ F (only the 16 BindExceptions never reach Mock) |
| I | Mock completed (`final` sent) | **37849** | — | `mockllm_completed_requests_total`; `cancelled = 0`, `failed = {}` |
| J | Gateway terminal | 37849 completed + 16 upstream_error | — | gateway metrics + `gateway.log` |
| K | client terminal (measurement) | — | **30719** = 24440 completed + 6279 zero-event | `client_completed_total` + `client_zero_event_terminal_total` (invariant OK) |

### The two deficits — both closed, same root cause

```
k6 iterations started (whole)                46080
  ├─ LEG A  connect OK  → Gateway            37865   = C = F   (every SSE-open success reached the Gateway and started a VT task)
  └─ LEG A  connect FAIL (EADDRNOTAVAIL,      8215   = B − C   (measurement share 6279; silent, no error callback)
            silent at the k6→:18102 leg)

Gateway VT started (whole)                   37865
  ├─ Gateway→Mock  OK                        37849   = I       (Mock completed; cancelled 0, failed 0)
  └─ Gateway→Mock  BindException (EADDRNOTAVAIL) 16   = F − I   = gateway.log BindException count exactly
```

- **LEG-B (Gateway→Mock) deficit = exactly 16**, fully accounted by the 16 `BindException`s
  (`F − I = 37865 − 37849 = 16`). Mock saw and completed everything else; it never cancelled or
  failed a single request.
- **LEG-A (k6→Gateway) deficit = 6279 (measurement) / 8215 (whole)** — the dominant unexplained
  population in the canonical run — is now explained: `open_attempt` fired, `open_success` did not,
  `on('error')` never fired. `F == C` exactly (37865): every LEG-A success reached the Gateway;
  every LEG-A failure never did.
- **Both deficits are `EADDRNOTAVAIL` from the same exhausted host ephemeral-port pool**
  (A ∪ B unique ports peak 16362 / 16384; ∩ ≈ 1; every failure second coincides with the ceiling —
  see `m2-r256-diagnostic-result.md` §time-series correlation).

## Direct Mock R=256 (`direct-mock-r256/`) — reference (single leg, no Gateway)

| # | Population | Whole run | Measurement cohort |
|---|---|---|---|
| A | k6 iterations started | 46080 | 30716 |
| B | SSE open attempted | 46080 | — |
| C | SSE open succeeded | **46080** | — |
| D | first event received | **46080** | — |
| I | Mock completed | **46080** | (cancelled 0, failed 0) |
| K | client terminal | — | 30716 completed + **0** zero-event |

**Zero deficit at any stage.** Single-leg peak port occupancy 10324 / 16384 (63%), ~6060 free →
no `EADDRNOTAVAIL`. Establishes that LEG A in isolation is not the problem — the M2 failure requires
both legs on the same host simultaneously.

## Comparison to canonical `unit8-open-screening/m2-r256-screen1` (§18)

| quantity | canonical | fresh diagnostic | note |
|---|---|---|---|
| measurement started | 30721 | 30719 | same offered load |
| completed | 25033 | 24440 | — |
| zero-event / failed_no_event | 5688 | 6279 | slightly worse (sampler host pressure) |
| VT started − Mock completed | 9 | 16 | == BindException count in both |
| BindException | 9 | 16 | same signature, same `e.toString()`-only log |
| `sse error` console lines | 0 | 0 | same silent LEG-A failure |
| virtual_tasks_active peak | 2090 | 2341 | non-binding in both |
| virtual_tasks_active trend | flat | declining | not rising in either — no backlog |
| tomcat_connections peak | 2092 / 3200 | 2343 / 3200 | non-binding in both |
| process_cpu_usage peak | 0.09 | 0.137 | idle in both |

Semantics identical; the fresh run reproduces the canonical `UNCLASSIFIED_MODEL_FAILURE` and the
added two-leg socket telemetry supplies the mechanism.
