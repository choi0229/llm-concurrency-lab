# Phase 4 Open Unit 9 — 12-run Formal Matrix: COMPLETE

**Open Formal = PASS.** 12 initial valid runs, frozen order, 0 retries, 0 extras, 0 STOP,
0 provenance drift. Protocol authority: `docs/test-plan/phase4-open-formal-protocol.md`.
Aggregate: `formal-aggregate.json`. State: `formal-summary.json`. Provenance:
`provenance-formal-start.json` (re-verified at end — every frozen file hash identical).

## Cells

| cell | model | rate | predicate | valid reps | boundary PASS | signature (colour) | confirmation |
|---|---|---|---|---|---|---|---|
| F1 | M1 | R6 | `sustainable == true` | 3/3 | **3/3** | GREEN 3/3 | **CONFIRMED SUSTAINABLE** |
| F2 | M1 | R7 | `sustainable == false` | 3/3 | **3/3** | **AMBER 3/3** | **CONFIRMED** |
| F3 | M2 | R168 | `GREEN == true` | 3/3 | **3/3** | GREEN 3/3 | **CONFIRMED SUSTAINABLE** |
| F4 | M3 | R168 | `GREEN == true` | 3/3 | **3/3** | GREEN 3/3 | **CONFIRMED SUSTAINABLE** |

Every one of the 12 runs: VALID, `dropped_iterations = 0`, `reliability_pass = true`,
`stall_detected = false`, `clock_integrity_ok = true`, `postflight_clean = true`,
0 `BindException`, 0 `EADDRNOTAVAIL`, `time_wait_75s_post_drain ≤ 2` (gate PASS).

## Per-cell numeric (median across 3 valid reps)

| metric | F1 (M1 R6) | F2 (M1 R7) | F3 (M2 R168) | F4 (M3 R168) |
|---|---|---|---|---|
| actual arrival /s | 6.00 | 7.00 | 168.00 | 168.00 (max 170.3) |
| completion ratio | 1.000 | 1.000 | 1.000 | 1.000 |
| completed TTFC p95 (s) | 1.006 | **40.65** | 1.028 | 1.022 |
| completed duration p95 (s) | 7.87 | **47.51** | 8.07 | 8.06 |
| backlog trend (1st→2nd half) | 47 → 46 (flat) | **177 → 241 (rising)** | 1338 → 1306 (declining) | 1338 → 1303 (declining) |
| platform threads peak | 138 | 138 | 110 | 30 |
| CPU (cores) | 0.060 | 0.062 | 0.391 | 0.328 |
| RSS peak (MiB) | 275 | 350 | 1159 | 450 |
| FD open peak | 108 | 377 | 2756 | 2791 |
| M1 executor active peak | 47/50 | **50/50** | — | — |
| M1 executor queue depth peak | 0 | **263** | — | — |
| M1 executor rejected | 0 | **0** | — | — |
| M2 virtual_tasks_active peak | — | — | 1370 | — |
| M2 Tomcat connections peak | — | — | 1372 / 3200 | — |
| M3 reactor active peak | — | — | — | 1370 / 3200 |
| M3 reactor pending peak | — | — | — | 0–1 (transient) |

## Interpretation (frozen wording)

- **M1: Formal-confirmed Open MSAR bracket = [6, 7].** F1 CONFIRMED SUSTAINABLE + F2 CONFIRMED.
  F2's expected AMBER signature reproduced **3/3** (strongest reproduction): at R7 M1 saturates —
  executor 50/50 busy, queue depth ~263 (bounded, 0 rejection so reliability holds), TTFC/duration
  p95 blow out to ~40/48 s, backlog rises through the window → `MODEL_SATURATION` shape, distinct
  from R10's `MODEL_REJECTION` RED.
- **M2: Open MSAR ≥ 168, CONTROL_CENSORED** (F3 CONFIRMED SUSTAINABLE). Not `MSAR = 168`.
- **M3: Open MSAR ≥ 168, CONTROL_CENSORED** (F4 CONFIRMED SUSTAINABLE). Reactor pool non-binding
  (pending peak 0–1 transient). Not `MSAR = 168`.
- **M2 vs M3 exact Open ceiling ranking: INCONCLUSIVE** — both control-censored at
  `SAFE_OPEN_MAX_SINGLE_HOST_FINAL = 168`. No `M2 = M3`, no "VT ceiling = WebFlux ceiling", no
  "168 is the architecture limit".
- Closed (concurrency ownership) and Open (arrival sustainability) boundaries stay separate
  experiments — not combined into one number in this Unit.

## Exclusions confirmed

Canonical Formal replicate set = the 12 `f{1..4}-…-rep{1,2,3}` dirs only. Excluded from the
aggregate: both Canary dirs (`m2-r168-canary`, `m2-r168-canary-gatefix-replacement`), all Screening
(`unit8-open-screening/`), Unit 8.2 calibration (`unit8.2-…/`), Unit 8.1 diagnostics.
