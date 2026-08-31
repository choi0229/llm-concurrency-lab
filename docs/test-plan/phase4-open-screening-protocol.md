# Phase 4 Part B — Open-model Screening Protocol Freeze

Frozen before any Open Gateway load runs. Reuses existing frozen definitions wherever they exist
(`docs/decisions/phase4-scalability-definition.md`) rather than inventing new ones; freezes only
what that ADR explicitly left open.

## 1. Reused, already-frozen (not re-decided here)

- **SAFE_OPEN_MAX = 256 req/s** (`unit3-control-calibration/open-control-summary.json`,
  `robust_clean_ceiling_R=320 / 1.25`). Confirmed unchanged: `load-test-k6/scenarios/
  03-constant-arrival-rate-continuous.js` and `mock-llm-fastapi/app/main.py` both unchanged since
  that calibration (mtime check) — no re-calibration performed, per
  `phase4-scalability-definition.md` §16-2/16-3 (reuse allowed when script/source unchanged).
- **MSAR** definition (§5): dropped=0, unexpected failure=0, timeout=0, crash=0, control valid,
  completion throughput >= 98% of actual arrival rate, no sustained backlog growth (§6, exact
  slope deferred — see §3 below), TTFC p95<=2.0s, duration p95<=10.0s (completed-only, §2-1),
  postflight clean.
- **R_LOW / R_MSAR / R_OVER** (§13) — the frozen names; no new acronym invented.
- **GREEN/AMBER/RED** (§7): GREEN = MRC-equivalent PASS + SLO PASS; AMBER = reliability holds but
  SLO fail or clear backlog growth; RED = rejection/timeout/unexpected failure/crash/control
  ceiling (environment/control failure is INVALID, not RED, per the same section).
- **Client-cohort-authoritative principle** (`docs/decisions/phase2-formal-linux-environment.md`
  §"Client cohort accounting"): k6's own measurement-phase counters are the authoritative cohort;
  Prometheus is diagnostic only, never forced into exact equality with the client cohort.
- **Initial screening rates**: 2, 5, 10, 20, 40, 80, 160, 256 (§10 candidate list capped at
  SAFE_OPEN_MAX, not 320 — 320 was Unit 3's own control-only probe above the frozen ceiling).

## 2. Newly frozen here (not previously defined)

### 2.1 Warmup / measurement windows

No prior freeze existed for Open Screening/Formal durations (Unit 3's own 15s/30s windows are
explicitly labeled calibration-only in that script's harness). Frozen now, before any load:

- **Screening**: warmup=60s, measurement=120s.
- **Formal**: warmup=120s, measurement=300s.

Single continuous k6 process per run (warmup immediately followed by measurement, no restart/drain
between phases — this is exactly what `05-phase4-open-arrival-rate.js`'s continuous design
already provides). No Gateway/Mock/Prometheus restart between phases.

### 2.2 M3 Open ConnectionProvider headroom

The Closed `WEBCLIENT_MAX_CONNECTIONS=800`/`1400` values are sized for a fixed simultaneous
concurrency target and are not reused for Open load, where concurrency is a function of arrival
rate and in-flight duration, not a directly-set knob.

```
OPEN_M3_MAX_CONNECTIONS = ceil(SAFE_OPEN_MAX * SLO_duration_s * headroom)
                        = ceil(256 * 10.0 * 1.25)
                        = 3200
OPEN_M3_PENDING_ACQUIRE_MAX_COUNT = 3200 (same headroom policy: sized equal to maxConnections)
```

**Why SLO duration (10.0s), not nominal service time (~7.8s)**: the existing Unit 3
`little_law_sanity_check_R320` note used ~7.8s only as a rough sanity expectation, explicitly "not
a capacity determinant." For an actual configured ceiling, the goal is that the pool never becomes
an artificial limiter for any request that could still legitimately meet the SLO — using the SLO
threshold (10.0s) as the concurrency-budget multiplier is the more conservative, defensible choice
or a request stretched all the way to the SLO boundary would itself start contending for
connections that a nominal-duration-sized pool wouldn't have budgeted for. This decision is fixed
before any Open Gateway load, not adjusted after seeing results.

`WEBCLIENT_PENDING_ACQUIRE_TIMEOUT_MS`/`WEBCLIENT_CONNECT_TIMEOUT_MS` unchanged (10000ms/3000ms).

### 2.3 Backlog — diagnostic only for Screening; exact slope deferred

Per `phase4-scalability-definition.md` §6, the exact backlog-growth-slope computation is
explicitly reserved for "Open Formal Protocol (Unit 7.5)" — i.e., it is not to be invented ahead of
time here, either. For **Screening**, backlog is recorded per-model (§6's architecture-neutral
definitions: M1 `executor_queue_depth`, M2 active-vs-expected-steady-state, M3
`gateway_active_requests` + connection diagnostics) and inspected qualitatively (measurement
back-half trend: rising vs. flat/falling) — never averaged into a single cross-model number, and
never used alone to flip GREEN/AMBER without also checking the frozen SLO/throughput conditions.
The exact numeric threshold proposed elsewhere in this project's working notes (an arrival-rate-
proportional slope bound) is **not adopted yet** — it will be frozen, informed by actual Screening
backlog shapes, immediately before Open Formal, exactly mirroring how Unit 5.5 froze Closed Formal
specifics only after seeing Closed Screening's actual boundary shape.

### 2.4 Clock/measurement-integrity cross-check (carried forward, not new)

Unit 3's own Open R=160 calibration already hit this exact class of issue: a custom JS `Date.now()`
Trend metric (`client_stream_duration_seconds`) showed an anomalous p95 (104.9s) while k6's
built-in `iteration_duration`/`http_req_duration` (Go-runtime-timed) stayed normal for the same
run (`unit3-control-calibration/open-control-summary.json`). This pre-dates and independently
confirms the same lesson Closed Formal's F6-rep3 clock-integrity forensics later re-derived. Open
Screening/Formal validity therefore requires cross-checking `client_completed_stream_duration_seconds`
/`client_ttfc_completed_seconds` (custom, Date.now()-based) against k6's built-in
`http_req_duration`/`iteration_duration` for the same run; a run where these disagree by more than
noise is treated as measurement-integrity-suspect exactly as F6-rep3 was, not as a model result.

## 3. Open harness / collector

New files (frozen Closed harness/collector never touched): `scripts/run-phase4-open-benchmark.sh`,
`scripts/collect_phase4_open_result.py`, paired with `05-phase4-open-arrival-rate.js`. Same
per-run lifecycle shape as Closed (fresh Gateway/Mock/Prometheus/k6, VALID/INVALID gate before any
color, postflight, cleanup) adapted for continuous arrival-rate load instead of a discrete wave.

## 4. Frozen files — no change after first Open Gateway load

Once Open Screening's first real load run starts: `05-phase4-open-arrival-rate.js`,
`run-phase4-open-benchmark.sh`, `collect_phase4_open_result.py`, this protocol document's §1/§2
values, Gateway/Mock source. A discovered issue is a STOP-and-report, not an in-flight patch.
