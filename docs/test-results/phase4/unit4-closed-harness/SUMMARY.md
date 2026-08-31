# Phase 4 Unit 4 (+ 4.5 + 4.6) — Closed-model Harness Verification Summary

**Scope reminder**: measurement-pipeline verification at N=20 only, across all three sub-units. Not
a scalability result — no M1/M2/M3 comparison, no MSC/MRC judgment. Unit 4.5 closed
plateau/stall-automation and CPU-formula-source open items; Unit 4.6 closed three
measurement-semantic gaps (target-vs-actual conflation, non-completed-only SLO population, missing
server-outcome classification) that could have misclassified real Unit 5 saturation results.
**Zero N>20 load was executed across all three sub-units.**

## 1. Harness — final state

- `scripts/run-phase4-closed-benchmark.sh` — fresh Mock + fresh Gateway + fresh Prometheus (1s
  scrape) + k6 wave + drain + snapshot (now including a **pre-wave** snapshot, Unit 4.6) +
  postflight + teardown.
- `scripts/collect_phase4_closed_result.py` — Closed-specific schema; separates measurement
  validity from model outcome (Unit 4.5); separates `target_concurrency` from `actual_started`,
  reads SLO latency from completed-only trends, computes server-outcome-evidenced
  GREEN/AMBER/RED + failure-taxonomy classification (Unit 4.6).
- `scripts/check_phase4_stall.py` — multi-signal environment-stall checker (Unit 4.5).
- `scripts/test_phase4_collector_synthetic.py` — 24 synthetic checks, all passing (Unit 4.5: 3,
  Unit 4.6: 21 additional).
- `load-test-k6/scenarios/04-phase4-closed-wave.js` — prewarm + wave-start timestamp (Unit 4) +
  outcome-tagged cohort (Unit 4.5) + explicit `actual_started` counter + completed-only latency
  Trends (Unit 4.6).
- `monitoring/prometheus/prometheus-phase4.yml` — 1s scrape interval, unchanged since Unit 4.

Full protocol: `docs/test-plan/phase4-closed-harness-protocol.md` (17 sections, all frozen).

## 2. Core verdict — all three models, N=20, final harness

| | M1 | M2 | M3 |
|---|---|---|---|
| target_concurrency / actual_started | 20 / 20 | 20 / 20 | 20 / 20 |
| target_reached | true | true | true |
| cohort (completed/rejected/failed_mid/failed_no_event) | 20/0/0/0 | 20/0/0/0 | 20/0/0/0 |
| invariant_ok | true | true | true |
| completed TTFC p95 | 1.032s | 1.038s | 1.041s |
| completed duration p95 | 7.887s | 7.904s | 7.891s |
| reliability_pass / slo_pass | true / true | true / true | true / true |
| **color / classification** | **GREEN** | **GREEN** | **GREEN** |
| server_outcomes_wave_estimate | completed=20, all others=0 | completed=20, all others=0 | completed=20, all others=0 |
| prewarm_contamination_ok | true | true | true |
| PID match / postflight / stall | true / clean / false | true / clean / false | true / clean / false |
| **valid** | **true** | **true** | **true** |

Full evidence: `m{1,2,3}-n20-harness-smoke/{result.json,validity.json,stall-check.json,...}`.

## 3. Unit 4.6 — the three gaps closed

### 3a. Target vs actual_started

Old collector used `target_concurrency (N)` directly as the cohort-invariant source — wrong,
because at higher screening N the load generator itself can under-deliver (a real `LOADGEN_LIMIT`),
and treating `N` as ground truth would silently misattribute that to the Gateway. Now:
`actual_started` is read from an explicit k6 Counter (`client_actual_started_total`, incremented as
literally the first line of every iteration — not inferred from a Trend's incidental `.count`).
`target_reached = (actual_started == target_concurrency)` is a hard validity gate;
`invariant_ok` is computed against `actual_started`, never `target_concurrency`.

**Synthetic proof**: `actual_started=80` against `target=100` → cohort's own accounting is
internally consistent (80==80) but `target_reached=false` → `valid=false`. A second fixture with
`actual_started==target==100` confirms the positive case.

### 3b. Completed-only SLO population

Old collector read TTFC/duration percentiles from the all-outcomes trend — a rejection's
near-instant "duration" could silently drag an aggregate p95 down, making a partially-failing run
look better than its completed-request reality. Now: two new k6 Trends
(`client_completed_ttfc_seconds`/`client_completed_stream_duration_seconds`) are populated **only**
when `outcome==='completed'`; the collector's `client_latency` block reads exclusively from these
as SLO-authoritative, keeping the all-outcome trends as diagnostic-only.

**Synthetic proof**: a 50-completed(~8s)/50-rejected(~instant) N=100 fixture yields
`completed_duration_p95_s≈8.0s` — exactly the completed population's true value, unaffected by the
50 near-instant rejected entries that would have polluted an aggregate. A `completed=0` fixture
confirms `completed_ttfc_p95_s`/`completed_duration_p95_s` are recorded as `null`, never fabricated.

### 3c. Server-side outcome classification (with prewarm-contamination handling)

Old schema had no way to distinguish *why* a non-completed client outcome occurred (timeout vs
upstream error vs a harness bug) beyond the client's own coarse taxonomy. Now: `gateway_requests_
total{outcome="..."}` is snapshotted **before** the wave (right after Gateway health-check, before
prewarm even starts) and **after** — the delta covers prewarm+wave combined. Prewarm is assumed to
always complete successfully (identical workload/path, just sequential); under that assumption,
`server_wave_completed_estimate = delta['completed'] - PREWARM_COUNT` should exactly equal the
client's completed count. When it does (`prewarm_contamination_ok=true`, confirmed in all three
real Unit 4.6 runs), all non-`completed` deltas are attributed wholly to the wave for
classification; when it doesn't, the run is marked invalid rather than silently mis-attributing a
prewarm failure.

A `RED` run (reliability_pass=false) is sub-classified from this evidence, conservatively:
`rejected>0`→`MODEL_REJECTION`, `timeout>0`→`MODEL_TIMEOUT`, `client_disconnect>0`→
`UNEXPECTED_CLIENT_DISCONNECT` (flagged for investigation, not treated as normal), `upstream_error>0`
with nothing else→`UPSTREAM_ERROR_UNCONFIRMED_CAUSE` (cause not asserted without Mock/control
corroboration), and — critically — **no server evidence at all for an observed client failure**
→`UNCLASSIFIED_MODEL_FAILURE`, never forced into a specific label the evidence doesn't support.

**Synthetic proof** (5 fixtures): a 20/80-rejected-style N=100 case and a 550/50-rejected N=600
case both → `MODEL_REJECTION`; an 80-completed/20-timeout N=100 case → `MODEL_TIMEOUT`; a
40-completed/10-failed-no-event N=50 case **with zero server-side evidence** →
`UNCLASSIFIED_MODEL_FAILURE` (proving the classifier doesn't default to `MODEL_TIMEOUT` just
because a client failure exists); an AMBER case (reliability maintained, TTFC p95=2.5s>2.0s
threshold) correctly produces `color=AMBER`, distinct from GREEN and RED.

## 4. GREEN / AMBER / RED — mechanically computable, MSC/MRC-ready

```
reliability_pass = (completed == actual_started) AND target_reached
slo_pass         = completed TTFC p95<=2.0s AND completed duration p95<=10.0s
GREEN = reliability_pass AND slo_pass
AMBER = reliability_pass AND NOT slo_pass
RED   = NOT reliability_pass   (only for valid=true runs)
```

Both `reliability_pass` and `slo_pass` are independent booleans in every `result.json`, so Unit 5's
screening loop can derive both the MSC bracket (highest GREEN / lowest non-GREEN) and the MRC
bracket (highest GREEN-or-AMBER / lowest RED) mechanically from a sequence of results — no
additional judgment calls needed at screening time (full rationale: `docs/test-plan/
phase4-closed-harness-protocol.md` §17).

## 5. Synthetic test suite — 24/24 PASS

`scripts/test_phase4_collector_synthetic.py`: clean-fixture GREEN, actual_started-vs-target (both
directions), completed-only latency (both the pollution-avoidance case and the completed=0→null
case), MODEL_TIMEOUT, MODEL_REJECTION (×2), UNCLASSIFIED_MODEL_FAILURE, prewarm-contamination
detection, PID mismatch, control-range violation — plus an ad-hoc AMBER verification run
separately confirmed. All pass; full output captured in this Unit's session log.

## 6. Unit 4.5 items — unchanged, reconfirmed

Stall checker (Gateway-gap>5s, Mock-gap>15s, client-duration-anomaly>20s, correlation rule) and CPU
formula (`avg_cores = mean(process_cpu_usage) * availableProcessors`, source-confirmed via
bytecode inspection of the exact `micrometer-core-1.17.0.jar`) — **not revisited this Unit**, both
still frozen exactly as Unit 4.5 left them. `stall_detected=false` reconfirmed in all three fresh
Unit 4.6 N=20 runs.

## 7. Bugs found/fixed this Unit

None in already-shipped logic — Unit 4.6's three closures were planned schema corrections (per the
approval feedback), not bug fixes discovered independently. The rewritten collector and amended k6
script both passed their real N=20 smokes and all synthetic tests on first full integration.

## 8. Remaining risk

1. `UPSTREAM_ERROR_UNCONFIRMED_CAUSE` sub-classification (whether it's `DOWNSTREAM_LIMIT`, a
   control problem, or genuine saturation) is not automated — deferred to Unit 5's screening loop
   to cross-check against Mock's own metrics if/when this outcome is actually observed.
2. Start-spread, CPU-formula, and the new classification logic are all still verified only at
   N=20 — Unit 5 is the first real test of this schema under actual saturation-shaped results.

## 9. SAFE_CLOSED_MAX / control-censored policy — unchanged

`SAFE_CLOSED_MAX = 640`. Enforced automatically (`control_range_ok` in the validity gate, verified
by synthetic test). Control-censored policy (`docs/decisions/phase4-scalability-definition.md`
§16) unchanged.

## 10. Overall PASS/FAIL

**Unit 4 core harness: PASS. Unit 4.5 closure: PASS. Unit 4.6 closure: PASS.** All three
measurement-semantic gaps closed with real evidence (fresh N=20 smokes across all three models +
24 synthetic fixture tests), zero N>20 load executed across any of the three sub-units. **Closed
Harness declared FINAL UNIT 5 SCREENING FREEZE** (`docs/test-plan/phase4-closed-harness-protocol.md`
§14). **Unit 5 Closed Screening readiness: PASS.**
