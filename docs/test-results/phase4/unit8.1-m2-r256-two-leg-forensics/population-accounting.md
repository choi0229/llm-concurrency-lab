# R=256 population accounting — from existing `m2-r256-screen1` raw only

§15 table. **Only measured values. Missing rows are marked — not guessed.**
"Whole process" = warmup + measurement (46081 iterations); "measurement cohort" = 30721.

| # | Population | Value | Source |
|---|---|---|---|
| A | k6 iterations started (measurement cohort) | **30721** | `result.json` `client_cohort.actual_started`; `measurement_iterations_started_total` |
| A' | k6 iterations started (whole run) | 46081 | `k6-summary.json` `iterations.count` |
| B | SSE open attempted | **not separately instrumented** in scenario 05 — `http_reqs = 46081` (whole run) is the closest proxy: one HTTP request object per iteration | `k6-summary.json` `http_reqs` |
| C | SSE open succeeded (xk6-sse `on('open')`) | **not instrumented in scenario 05** | — (scenario 06 adds `client_sse_open_success_total`; not used for canonical points) |
| D | first SSE event observed | **25033** (only completed iterations reached first event; `client_ttfc` has 25033 observations, all ≈1.0 s) | `k6-summary.json` `client_ttfc_completed_seconds` count = `client_completed_total` |
| — | zero-event terminal (`failed_no_event`) | **5688** | `result.json`; `k6-summary.json` `client_failed_no_event_total` |
| E | Gateway request reached (`/chat/stream` entered) | **≈ 38472** whole process (`virtual_tasks_started_total` final) / measurement-window rise ≈ 30721 − (LEG-A connect failures) | `result.json` `implementation_specific.virtual_tasks_started_final = 38472` |
| F | Gateway outbound attempted (`upstreamStarted`) | **not exported as a distinct counter**; `gateway_active_requests` peak 2090 = `virtual_tasks_active` peak 2090 → effectively every request that entered also started upstream | `prom-gateway_active_requests.json`, `prom-virtual_tasks_active.json` |
| G | Mock request reached (`stream requested`) | **≈ 38463 + in-flight** — `mock-metrics-final` `mockllm_completed_requests_total = 38463`, `active_final = 0` | `result.json` `mock.completed_final = 38463` |
| H | Mock completed (`final` sent) | **38463** | same |
| H' | Mock cancelled / failed | `cancelled` and `failed` **not captured into `result.json`** — need `mock-metrics-final.txt` parse (see below) | `mock-metrics-final.txt` |
| I | Gateway terminal | whole-process estimate: completed 38463, upstream_error 9, all others 0 | `result.json` `server_outcomes_whole_process_estimate` |
| J | client terminal (measurement cohort) | 25033 completed + 5688 failed_no_event = **30721** (invariant OK) | `result.json` `client_cohort` |

## The gap

- **LEG A (k6→Gateway) deficit:** A − (reached Gateway) ≈ **5688** iterations started at k6 but
  produced no SSE event and no error. `http_req_duration.min = 486µs` and the total absence of
  `on('error')` firings is the Unit 7.2/7.4 connect-stage-failure signature. **This is the dominant
  failure and it is on LEG A.**
- **LEG B (Gateway→Mock) deficit:** only **9** (the `BindException`s → `upstream_error = 9`).
  Mock `completed = 38463` vs Gateway `virtual_tasks_started = 38472` → difference 9. **LEG B is
  almost fully accounted for; it is not where the 5688 went.**

## Not answerable from existing raw (needs the Unit 8.1 live diagnostic)

- B, C explicit counts (scenario-05 has no `open_attempt` / `open_success` counters).
- Per-leg socket state (ESTABLISHED / SYN_SENT / TIME_WAIT / CLOSE_WAIT) for either leg — the
  canonical harness does **not** run the socket sampler.
- Unique local ephemeral-port counts per leg, union, and intersection.
- Mock `cancelled` / `failed` breakdown at the second (needs `mock.log` time-series parse; the
  10 MB `mock.log` is present but not yet reduced).
- Whether the LEG-A connect failures coincide in time with LEG-B `BindException` seconds and with a
  combined ephemeral-port peak (the core §19 shared-host-confound test).
