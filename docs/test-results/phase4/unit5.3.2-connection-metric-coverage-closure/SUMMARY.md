# Phase 4 Unit 5.3.2 — Connection Metric Coverage Closure

No new load was generated for the closure itself. Closes the two remaining fail-closed gaps in
`connection_limit_binding`/`connection_limit_evaluable`: (1) timestamp-intersection silently
dropping a missing sample instead of failing closed, and (2) `max_connections_configured` being
read via a first-match-only lookup with no cardinality/identity/value check.

## 1-2. Timestamp coverage (evidence-grounded, not assumed)

Confirmed from raw for all 4 existing M3 artifacts (N=50/100/200/400): the pending and active
wave-window timestamp **sets** are exactly identical (8-9 samples each, `pt==at` True in every
case) — expected, since both come from the harness's single `query_range` call shape (same
start/end/step=1s). `timestamp_alignment_ok` is now computed as **exact set equality**
(`pending_wave_ts == active_wave_ts`), not an intersection that would silently drop a
one-sided-missing sample. No nearest-neighbor/±1s/interpolation tolerance was implemented.

## 3. Empty-wave coverage

`connection_limit_evaluable = timestamp_alignment_ok AND len(common_wave_ts) > 0` — two empty
sets are trivially "equal" but zero common samples now independently fails evaluability, so a
run whose only samples land in prewarm/drain never reads as "no binding."

## 4. New diagnostic fields

`pending_wave_sample_count`, `active_wave_sample_count`, `common_wave_sample_count`,
`timestamp_alignment_ok` added to `reactor_connection_pool`.

## 8-11. max_connections evaluability (checked against real raw)

Confirmed from raw: `reactor_netty_connection_provider_max_connections` carries the *same*
`id`/`name`/`remote_address` labels as pending/active within each run (verified for all 4 M3
artifacts — the `id` value itself varies run-to-run as a per-JVM-instance hash, but always
matches across all three metrics *within* a run). `metric_line()` (first-match-only) was replaced
for this evaluator by `text_metric_series()`, which returns every matching line so an unexpected
second series is visible. `connection_limit_evaluable` now additionally requires
`max_connections_series_count==1`, `max_connections_identity_match` (same `(name, remote_address,
id)` as the pending/active pool), and `max_connections_value_ok` (`==` the frozen
`M3_FROZEN_WEBCLIENT_MAX_CONNECTIONS=800`, not just `>0`) — a runtime/frozen-config mismatch is
now itself an evaluability failure.

## 5-7, 12-13. Synthetic coverage

`scripts/test_phase4_collector_synthetic.py`: **93/93 PASS, 0 FAIL** (77 pre-existing
[Unit 5.3 + 5.3.1] + 16 new checks across 5 new fixture cases):

| Case | Setup | Result |
|---|---|---|
| 15 Timestamp mismatch | active series missing one wave-window sample pending has | `timestamp_alignment_ok=False`, evaluable=False, valid=False |
| 16 Empty wave coverage | all samples in prewarm/drain, zero in wave window | `common_wave_sample_count=0`, evaluable=False (not read as "no binding") |
| 17 max_connections multi-series | max metric returns 2 lines | `max_connections_series_count=2`, evaluable=False |
| 18 max_connections identity mismatch | max metric's pool labels differ from pending/active | `max_connections_identity_match=False`, evaluable=False |
| 19 max_connections value mismatch | runtime value=100, frozen expected=800 | `max_connections_value_ok=False`, evaluable=False |

`scripts/test_phase4_screening_driver_synthetic.py`: **33/33 PASS, 0 FAIL** (unchanged from Unit
5.3.1 — `NO_RETRY_KEYS` already carried both keys).

## 14. Temporal-coupling logic unchanged

`binding_sample(t)`, the `>=3`-consecutive-sample threshold, the wave window definition, and the
pending/active metric semantics are all byte-identical to Unit 5.3.1 — this unit only added
coverage/evaluability gates around them, per instruction.

## Existing artifact offline regression (final closure logic, no new load)

All 12 raw points re-collected via the final collector:

- **M1/M2 (all 8 runs)**: byte-for-byte identical to the true original `result.json` (confirmed
  via `diff`).
- **M3 N=50/100/200**: unchanged, `valid=True`, `color=GREEN`, `connection_limit_evaluable=true`,
  `timestamp_alignment_ok=true`.
- **M3 N=400**: unchanged verdict, `valid=True`, `color=GREEN`,
  `connection_limit_evaluable=true`, `timestamp_alignment_ok=true`,
  `common_wave_sample_count=9`, `max_connections_series_count=1`,
  `max_connections_identity_match=true`, `max_connections_value_ok=true`,
  `binding_max_consecutive_samples_wave=0`, `connection_limit_binding=false`.

No STOP condition (§20 of the governing instruction) was triggered: timestamp alignment holds for
all 4 M3 artifacts, max-metric identity is safely evaluable and matches, no existing M3 raw
reclassified to INVALID under the new gates, M1/M2 numerically unaffected, and both synthetic
suites pass in full.

## Option A promotion — executed

Per the governing instruction ("closure가 모두 PASS하면 추가 사용자 승인 없이 Option A를
실행해도 된다"):

- `screening_epoch = post-timeout-fix-pending-semantics-v2.1`
- 11 of 12 points needed no change (amended == true original, byte-identical).
- `m3-n400-screen1`: the true pre-closure original `result.json`/`validity.json` were preserved as
  `result-preclosure-original.json`/`validity-preclosure-original.json` in the same directory
  (history retained, per instruction), and the final amended result
  (`result-amended-recollect.json`/`validity-amended-recollect.json`) was promoted to the
  canonical `result.json`/`validity.json` — this is the file `resume_from_existing()` and all
  downstream tooling read.

## Final freeze

Declared: **UNIT 5 CLOSED SCREENING FINAL FREEZE** on `scripts/collect_phase4_closed_result.py`
and `scripts/run_phase4_closed_screening.py` production classification/orchestration logic (their
synthetic test files may still grow with coverage, but the classification semantics themselves —
wave window, sustained/binding definitions, evaluability gates, NO_RETRY_KEYS — do not change
before N=640/refinement). Any new correctness/measurement issue discovered from here forward is
an immediate Unit 5 STOP, not an in-flight patch.

## Unit 5 resume

Authorized per the governing instruction (all closure conditions passed). Canonical state at
resume:

- M1: N=50 GREEN, N=100 AMBER, N=200 AMBER, N=400 RED(MODEL_TIMEOUT) — inactive for further
  geometric escalation, MSC bracket [50,100], MRC bracket [200,400], pending refinement.
- M2: N=50/100/200/400 all GREEN — active, next candidate N=640.
- M3: N=50/100/200/400 all GREEN (N=400 now canonical after promotion above) — active, next
  candidate N=640.

Next actual load (executed after this document): M2 N=640 -> M3 N=640 (M1 N=640 excluded, already
known-RED at N=400), followed by M1 MSC/MRC refinement, using the previously-approved
SAFE_CLOSED_MAX=640 / control-censoring / midpoint-refinement (<=20% width or 4 extra points)
rules unchanged.
