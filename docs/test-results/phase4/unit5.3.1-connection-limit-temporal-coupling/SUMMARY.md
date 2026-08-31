# Phase 4 Unit 5.3.1 — Reactor Connection-Limit Temporal Coupling Closure

No new load was generated. This unit closes two semantic gaps in the Unit 5.3
`connection_limit_binding` implementation, adds synthetic coverage, and offline re-evaluates the
existing 12 canonical raw artifacts. No original `result.json`/`validity.json` was overwritten
(verified by mtime — see §"Existing artifact preservation").

## 1. Timestamp-grid evidence (checked, not assumed)

Confirmed directly from raw JSON for all four existing M3 artifacts (N=50/100/200/400): the
`pending_connections` and `active_connections` series always have **identical timestamp sets**
(same count, same values, same query grid) and always come back as **exactly one series each**,
with matching `id`/`name`/`remote_address` labels between the two metrics. This is because both
are fetched by the harness's single `query_range` loop with the same `start`/`end`/`step=1`
parameters against the same target. No nearest-timestamp tolerance was needed or implemented —
the closure pairs samples by exact-timestamp intersection (`set(pending_map) & set(active_map)`),
which degrades safely (fewer paired samples, never a fabricated pairing) if grids ever diverge.

## 2-4. Final semantics (frozen)

- **`sustained_pending`** (diagnostic, unchanged from Unit 5.3): `pending>0` for
  `>=SUSTAINED_PENDING_MIN_CONSECUTIVE_SAMPLES` (3) consecutive wave samples. Can be `True` while
  `connection_limit_binding` is `False` — this is now explicitly exercised (Case 7, Case 9 below).
- **`connection_limit_binding`** (validity-relevant): time-aligned — at each wave sample `t`,
  `binding_sample(t) = pending(t)>0 AND active(t)>=max_connections_configured`; `True` when
  `binding_max_consecutive_samples_wave >= 3`. A sustained pending period and an unrelated later
  peak in `active` can no longer combine into a false positive (the exact bug in the brief's own
  worked example — Case 7 below reproduces it and confirms the closure prevents it).
- **`connection_limit_evaluable`**: `True` only if pending series count == 1, active series count
  == 1, both series' `(name, remote_address, id)` match, and `max_connections_configured` is a
  positive number. Any failure -> `connection_limit_evaluable=False`, and
  `connection_limit_binding` is reported as `None` (never fabricated as `False`).

Implemented in `m3_connection_pool_evaluation()` (`scripts/collect_phase4_closed_result.py`,
replacing the previous `range_query_window_stats`-based independent-peak computation).

## 5. Series cardinality / 6. label identity policy

Fail-closed, as instructed: if either metric returns anything other than exactly one series, or
the two series' pool identity (`name`, `remote_address`, `id`) doesn't match,
`connection_limit_evaluable=False`. No cross-series aggregation (sum/max) was implemented — Phase
4's M3 architecture is a single ConnectionProvider against a single Mock remote, and no evidence
was found (in this or the Unit 5.2 forensics pass) that multiple series can legitimately occur, so
the safer fail-closed policy was adopted rather than inventing an aggregation semantic.

## 7. Validity gate — two separate reasons

```
"m3_connection_metrics_evaluable": <bool>,          # missing/malformed safety signal
"m3_connection_limit_binding_absent": <bool>,        # actual cap-binding evidence
```

When unevaluable, `m3_connection_limit_binding_absent` is reported as a vacuous pass (`True`) —
it is the *other* key that fails the run; this key never independently asserts "binding ruled out"
when it wasn't measured. Both are `NO_RETRY_KEYS` in `scripts/run_phase4_closed_screening.py`, each
with its own log message (`REACTOR_CONNECTION_LIMIT` vs. `REACTOR_CONNECTION_METRICS_UNEVALUABLE`).

## 8. Result.json schema (M3, final)

```json
"reactor_connection_pool": {
  "max_connections_configured": 800.0,
  "pending_series_count": 1, "active_series_count": 1, "series_identity_match": true,
  "connection_limit_evaluable": true,
  "active_peak_wave": 400.0,
  "pending_peak_wave": 1.0, "pending_nonzero_samples_wave": 1, "pending_max_consecutive_samples_wave": 1,
  "sustained_pending": false,
  "binding_nonzero_samples_wave": 0, "binding_max_consecutive_samples_wave": 0,
  "connection_limit_binding": false
}
```

## 9-10. Synthetic test results

`scripts/test_phase4_collector_synthetic.py`: **77/77 PASS, 0 FAIL** (23 pre-existing + 24 Unit
5.3 + 30 new Unit 5.3.1 checks across 8 new fixture cases). None hardcoded from the real M3 N=400
artifact.

| Case | Setup | Result |
|---|---|---|
| 7 Temporal mismatch (the brief's own worked example) | pending nonzero t=1-3 (3 consecutive); active reaches configured cap only from t=7 onward | `sustained_pending=True`, `binding_max_consecutive_samples_wave=0`, **`connection_limit_binding=False`**, valid=True — the independent-peak false positive does NOT occur |
| 8 Real concurrent binding, at exactly the threshold | pending nonzero + active==cap simultaneously, t=1-3 (exactly 3) | `binding_max_consecutive_samples_wave=3`, `connection_limit_binding=True`, valid=False, reason present |
| 9 Partial overlap | pending nonzero t=1-3 (3 consecutive); active reaches cap only at t=2-3 (2 of the 3) | `sustained_pending=True`, `binding_max_consecutive_samples_wave=2`, **`connection_limit_binding=False`** (2 < frozen 3) |
| 10 Missing pending series | pending file omitted | `connection_limit_evaluable=False`, `connection_limit_binding=None`, valid=False, `m3_connection_metrics_evaluable` present, `m3_connection_limit_binding_absent` absent (no double-count) |
| 11 Missing active series | active file omitted | `connection_limit_evaluable=False`, valid=False |
| 12 Missing/invalid configured cap | `max_connections` line omitted | `connection_limit_evaluable=False`, valid=False |
| 13 Unexpected multi-series | pending metric returns 2 series | `pending_series_count=2`, `connection_limit_evaluable=False` (no silent `result[0]`), valid=False |
| 14 Pool-identity mismatch | pending and active series carry different pool labels | `series_identity_match=False`, `connection_limit_evaluable=False`, valid=False |

`scripts/test_phase4_screening_driver_synthetic.py`: **33/33 PASS, 0 FAIL** (29 pre-existing +
4 new: `m3_connection_metrics_evaluable` NO_RETRY_KEYS membership + immediate-STOP behavior).

## 11-14. Existing M3 offline re-evaluation (final closure logic)

Re-run from the same untouched raw evidence used in Unit 5.2/5.3 (no new load), result copied to
`result-amended-recollect.json` alongside the untouched original:

- **N=50/100/200**: `valid=True`, `color=GREEN`, `connection_limit_evaluable=true`, no pending
  anywhere — unchanged conclusion from Unit 5.3, now with the richer evaluability/identity fields
  populated.
- **N=400**: `valid=True`, `color=GREEN`. `connection_limit_evaluable=true`,
  `pending_series_count=1`, `active_series_count=1`, `series_identity_match=true`,
  `pending_max_consecutive_samples_wave=1` (still not sustained), **`binding_max_consecutive_samples_wave=0`**,
  `connection_limit_binding=false` — identical final verdict to Unit 5.3's amended result, now
  additionally confirmed via time-aligned evidence rather than independent peaks. Full JSON diff
  against the true original `result.json` shows exactly the same two changed regions as Unit 5.3
  (`valid`/`invalid_reasons`, and `implementation_specific.reactor_connection_pool` replacing the
  old `pending_anomaly` field) — every other field (client cohort, latency, resource metrics,
  mock, postflight, stall check) is byte-identical.

## 15. M1/M2 unaffected

All 8 existing M1/M2 runs (N=50/100/200/400 each) re-collected under the final collector: **all 8
are byte-for-byte identical** to the true original `result.json` (confirmed by `diff`, not
sampled). This closure only touches the M3 branch.

## Existing artifact preservation

Confirmed via mtime: every original `result.json`/`validity.json` under
`docs/test-results/phase4/unit5-closed-screening/` still carries its original collection
timestamp (21:38-21:49, matching the actual load run) — none was modified by this unit's offline
re-evaluation. Only the `result-amended-recollect.json`/`validity-amended-recollect.json` sibling
files were written/overwritten.

## 16. Collector/driver changed files

`scripts/collect_phase4_closed_result.py` (new `_single_series()` and
`m3_connection_pool_evaluation()`, M3 branch rewired, validity dict split into two keys),
`scripts/run_phase4_closed_screening.py` (`NO_RETRY_KEYS` gains
`m3_connection_metrics_evaluable`, two distinct log messages), plus the two synthetic test files.
No k6/Gateway/Mock/Prometheus/harness file touched (confirmed by mtime scan).

## 17. Threshold stability

`SUSTAINED_PENDING_MIN_CONSECUTIVE_SAMPLES=3` was **not** changed in this unit. This unit's scope
was temporal coupling and evaluability, not threshold re-litigation — per the amendment brief
already on record in `docs/test-results/phase4/unit5.3-m3-pending-validity-amendment/SUMMARY.md`.
For the honest record: this threshold was operationally frozen during the Unit 5.3 amendment
(itself a post-hoc correction discovered after Unit 5 screening had already started), and this
document is the second post-hoc amendment in the same area before Unit 5 resumes.

## 18. k6_wall_end_ms teardown-tail caveat (retained, not closed)

Unchanged from Unit 5.3: the wave upper bound (`k6_wall_end_ms`) can run ~150-230ms past the last
VU's actual terminal. No new instrumentation was added to tighten this. With the
`>=3`-consecutive-sample threshold for both `sustained_pending` and `connection_limit_binding`,
a sub-250ms tail cannot by itself manufacture a 3-sample run (each sample is 1s apart) — so this
caveat remains documented but does not block resume.

## 19. Freeze readiness

If this closure is approved: `collect_phase4_closed_result.py`,
`run_phase4_closed_screening.py`, and the production classification logic therein (not the
synthetic test files, which may still grow) are ready to be declared UNIT 5 SCREENING FINAL
FREEZE — no further semantic change before N=640/refinement, per the governing instruction.
**Not yet declared** — pending explicit approval.

## 20. Canonical epoch / Unit 5 resume readiness

Option A remains recommended and is now on firmer evidentiary footing: the same 12 raw points,
re-evaluated under the temporal-coupled, evaluability-gated collector, reproduce the identical
Unit 5.3 verdicts (M1/M2 unchanged, M3 N<=200 unchanged GREEN, M3 N=400 VALID+GREEN). Recommended
epoch label unchanged: `screening_epoch=post-timeout-fix-pending-semantics-v2` (or `.1` if the
user wants the closure itself reflected in the label) — **not yet promoted**, pending approval.

**Unit 5 resume: not started.** M3 N=400 not retried, N=640 not run, no M1/M2 refinement, no
`screening-summary.json` change. Per the resume plan already on record (Unit 5.3 report §Unit 5
resume plan), the next actual load — once approved — would be M2 N=640 and M3 N=640 (M1 excluded,
already at known RED), followed by M1 MSC/MRC refinement. None of that has been executed here.
