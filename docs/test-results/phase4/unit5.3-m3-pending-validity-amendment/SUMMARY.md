# Phase 4 Unit 5.3 — M3 Pending Validity Contract Amendment

No new load was generated. This unit designs, implements, and offline-regresses a collector
classification amendment only. `docs/test-results/phase4/unit5-closed-screening/` raw evidence
(k6/gateway/mock logs, Prometheus JSON, timestamps) was not touched by anything in this unit;
existing `result.json`/`validity.json` files were not overwritten (see §"Existing artifact
preservation" below).

## 1. Final pending metric semantic

`reactor_netty_connection_provider_pending_connections` continues to be collected exactly as
before (same Prometheus query, same field). Its meaning is unchanged from Unit 5.2 forensics
(bytecode-confirmed): count of `acquire()` calls currently waiting because no idle connection is
available. What changed is **how the collector uses it for validity**, not what it measures.

## 2. Wave measurement window (frozen)

`[client_wave_start_epoch_ms.min, k6_wall_end_ms]`, both already-recorded fields
(`k6-summary.json` / `timestamps.json`) — no new instrumentation, no manufactured margin. A plain
numeric timestamp comparison (`start_s <= t <= end_s`) against the existing Prometheus range-query
samples is sufficient; Unit 5.2 forensics confirmed this correctly separates prewarm/drain from
the canonical wave despite the 1s scrape grid vs. ms-precision client timestamps (see synthetic
Cases 5/6 below). Lifetime (process-start-to-drain-end) peaks are retained as diagnostics only —
they no longer feed validity.

Implemented in `range_query_window_stats()` (`scripts/collect_phase4_closed_result.py`).

## 3. Sustained-pending definition (frozen)

`sustained_pending = (max consecutive nonzero 1s samples within the wave window) >= 3`
(`SUSTAINED_PENDING_MIN_CONSECUTIVE_SAMPLES = 3`).

Rationale (not fit to this run's outcome): Prometheus scrape interval is frozen at 1s
(`monitoring/prometheus/prometheus-phase4.yml`). The Unit 5.2 forensics event was exactly 1
consecutive sample (a connection-pool ramp-up transient that resolved on the very next scrape) —
1 must not qualify as "sustained." The frozen workload's TTFC is ~1s and full stream duration is
~7.8s; 3 consecutive samples (>=2s of continuous nonzero pending) is comfortably above a
single-scrape blip while remaining well short of consuming a whole stream's lifetime, so it
would catch genuine multi-second contention without being tuned to any specific observed value.

## 4. Connection-limit-binding definition (frozen)

`connection_limit_binding = sustained_pending AND active_peak_wave is not None AND
max_connections_configured is not None AND active_peak_wave >= max_connections_configured`.

`max_connections_configured` is read directly from the Gateway's own already-collected
`reactor_netty_connection_provider_max_connections` metric (`gateway-metrics-final.txt`) — not
hardcoded, not fabricated; this metric was already present in every M3 run's raw evidence and
simply wasn't being read before. Priority A (exact equality) is used, per the amendment brief:
both `active_connections` and `max_connections` are exact integer gauges at 1s granularity, so no
near-cap fallback is needed or implemented.

## 5. Validity gate (implemented)

`m3_pending_no_anomaly` (peak>0 anywhere in the whole process lifetime -> INVALID) is **replaced**
by `m3_connection_limit_binding_absent` (`not connection_limit_binding`). Transient pending alone
never fails validity; it is retained as diagnostic under
`implementation_specific.reactor_connection_pool`. `NO_RETRY_KEYS` in
`scripts/run_phase4_closed_screening.py` renamed to match, with a dedicated
`REACTOR_CONNECTION_LIMIT` log message distinguishing a confirmed cap-binding STOP (a
control/configuration limit) from an ordinary environment/harness invalid.

No threshold was loosened to pass this specific run — see §"Result-fitting check" below.

## 6. New result.json schema (M3 only)

```json
"implementation_specific": {
  "reactor_connection_active_peak": {...},   // diagnostic, whole process lifetime (unchanged field)
  "reactor_connection_pending_peak": {...},  // diagnostic, whole process lifetime (unchanged field)
  "reactor_connection_pool": {
    "max_connections_configured": 800.0,
    "active_peak_wave": 400.0,
    "pending_peak_wave": 1.0,
    "pending_nonzero_samples_wave": 1,
    "pending_max_consecutive_samples_wave": 1,
    "sustained_pending": false,
    "connection_limit_binding": false
  }
}
```

The old `pending_anomaly` boolean field is removed (replaced by the richer `reactor_connection_pool`
block). No M1/M2 schema field changed.

## 7. Synthetic tests

`scripts/test_phase4_collector_synthetic.py` — added 6 new fixture cases (Cases 1-6 from the
amendment brief), none hardcoded from the real M3 N=400 artifact. **All 47 checks PASS** (23
pre-existing + 24 new; the pre-existing 23 checks are unaffected, confirming no M1/M2 regression):

| Case | Setup | Result |
|---|---|---|
| 1 Clean | active=400 wave, pending=0 | valid=True, sustained=False, binding=False |
| 2 Transient ramp | pending 0,1,0 (1 consecutive), active 1->400 | valid=True, sustained=False, binding=False, key absent from invalid_reasons |
| 3 Sustained, below cap | pending 4 consecutive nonzero, active=400 (cap=800) | sustained=True, **binding=False**, valid=True |
| 4 Cap binding | pending 4 consecutive nonzero, active=800=configured cap | sustained=True, **binding=True**, valid=False, key present in invalid_reasons |
| 5 Prewarm-only pending | pending nonzero only before wave_start | pending_peak_wave=0, lifetime peak still shows it (diagnostic), binding=False, valid=True |
| 6 Drain-only pending | pending nonzero only after wave_end | pending_peak_wave=0, lifetime peak still shows it (diagnostic), binding=False, valid=True |

`scripts/test_phase4_screening_driver_synthetic.py` — added a check that
`m3_connection_limit_binding_absent` (not the old name) is in `NO_RETRY_KEYS` and triggers an
immediate no-retry STOP with the `REACTOR_CONNECTION_LIMIT` message. **All 29 checks PASS** (26
pre-existing, unaffected + 3 new).

## 8. Existing-artifact offline re-evaluation

Every existing post-fix canonical run was copied to a scratch directory, re-run through the
amended collector, and the result copied back as a **new sibling file** —
`result-amended-recollect.json` / `validity-amended-recollect.json` — next to the untouched
original `result.json` / `validity.json` in each run's directory under
`docs/test-results/phase4/unit5-closed-screening/`. No original file was overwritten.

**M1 (N=50/100/200/400) and M2 (N=50/100/200/400): all 8 amended results are byte-for-byte
IDENTICAL to the originals** (`diff` confirmed zero output on every one). The amendment touches
only the M3-specific branch of the collector; this is a positive control confirming that.

**M3 N=50/100/200: unchanged outcome** — `valid=True`, `color=GREEN` in both the original and the
amended result, in all three. The only diff is additive schema (the new
`reactor_connection_pool` block appears; `pending_anomaly` is gone), never a change to
`valid`/`color`/`classification`/any client-cohort or latency field.

**M3 N=400 (the run under investigation): reclassifies from INVALID to VALID+GREEN.**

```
valid:  false -> true
invalid_reasons: ["m3_pending_no_anomaly"] -> []
reactor_connection_pool: {
  max_connections_configured: 800.0, active_peak_wave: 400.0,
  pending_peak_wave: 1.0, pending_nonzero_samples_wave: 1,
  pending_max_consecutive_samples_wave: 1, sustained_pending: false, connection_limit_binding: false
}
```

Every other field in the diff (`client_cohort`, `client_latency`, `common_gateway`, `mock`,
`cpu`/`rss`/`fd`/`heap`, `postflight_clean`, `stall_check`) is byte-identical to the original —
confirmed by full JSON diff, not spot-checked. `target=400`, `actual_started=400`,
`completed=400`, `rejected=failed_mid=failed_no_event=0`, `reliability_pass=true`,
`slo_pass=true` (`completed_ttfc_p95_s=1.09005`, `completed_duration_p95_s=8.08205`) — all
unchanged from the original report.

### Existing artifact preservation

Confirmed via filesystem mtime scan: the only files touched anywhere under
`docs/test-results/phase4/unit5-closed-screening/` in this unit are the 24 new
`result-amended-recollect.json` / `validity-amended-recollect.json` siblings. No existing
`result.json`, `validity.json`, or raw evidence file (k6/gateway/mock logs, `prom-*.json`,
`timestamps.json`, etc.) was modified.

## 9. Result-fitting check

None of the changes made are a threshold relaxed to pass this run:

- The window scope (wave-only) and the `>=3` sustained threshold were derived from
  `phase4-design.md` §7-1's pre-existing "지속적으로" wording, the frozen 1s scrape interval, and
  the frozen workload's own TTFC/duration shape (Unit 5.2 forensics + this unit's §3 rationale) —
  not reverse-engineered to make the M3 N=400 sample pass.
- `pending<=1`, "ignore 1s pending", "ignore pending at N=400", and arbitrary threshold values
  (2/5/10) were never implemented — the frozen amendment brief explicitly forbade all four, and
  none of them appear anywhere in the diff.
- The synthetic Case 3 (sustained pending, pool below configured cap) is explicitly still
  `connection_limit_binding=False` and `valid=True` — sustained pending by itself, without the
  pool actually reaching its configured ceiling, is *not* enough to invalidate a run. Only Case 4
  (sustained AND at the configured cap) is invalid. This distinction was designed before any
  result-driven pressure to pass N=400, and N=400's own `pending_max_consecutive_samples_wave=1`
  does not even reach the "sustained" bar, independent of the cap question.

## 10. M1/M2 result unaffected

Confirmed byte-for-byte identical for all 8 existing M1/M2 runs (§8 above). This amendment does
not touch M1/M2 in any way.

## 11. Canonical epoch recommendation: Option A

**Option A is recommended.** Rationale:

- Raw collection (k6, Gateway, Mock, Prometheus config, the run harness) is completely unchanged
  — only the collector's *offline classification* of already-collected pending-metric evidence
  changed.
- Every existing M1/M2/M3 N=50/100/200/400 raw artifact was successfully re-evaluated under the
  amended collector without any new load, and (per §8) produced either an identical result (M1,
  M2, M3 N<=200) or a corrected classification using the exact same underlying raw evidence (M3
  N=400).
- A full re-run (Option B) would not produce different raw evidence for these 12 points — the
  same Gateway/k6/Mock/Prometheus behavior would recur — so it would only spend load-generation
  time to re-derive numbers already fully accounted for, with no reduction in
  result-driven-rerun-bias risk (if anything, more risk, since a rerun after seeing this exact
  outcome is itself result-driven).
- Recommended epoch label: `screening_epoch = post-timeout-fix-pending-semantics-v2`, built by
  promoting the 12 already-collected raw points' amended results as canonical (not by rerunning
  them). **Not yet applied** — this is a recommendation pending explicit approval, per the
  governing instruction not to execute the epoch decision automatically.

## 12. N=20 Gateway regression: not required

The amendment changed `collect_phase4_closed_result.py` (offline post-processing of already-saved
Prometheus JSON and metrics text) and `run_phase4_closed_screening.py` (an orchestration key
rename plus a log message). Nothing that touches a live Gateway/Mock/k6/Prometheus process was
changed — no raw collection code, no k6 script, no Gateway or Mock source, no Prometheus config,
no run harness. Per the governing instruction ("raw collection 코드가 바뀌면 새 load 시작 전에
N20 regression 필요" / otherwise not required), no N=20 regression was run for this unit.

## 13. Amendment history (verbatim, for the permanent record)

> Unit 5 M3 N400에서 single-sample pending acquire가 invalidity를 유발했다. Offline forensic
> 결과 이 event는 canonical wave 시작 시 connection pool이 1→400으로 ramp-up하는 순간 발생한
> 실제 transient acquire wait였고, configured maxConnections=800 ceiling과 무관했다. 기존
> `pending_peak>0` validity rule이 Unit 1의 'sustained hidden limiter 방지' 의도보다 과도하게
> 강했음을 확인하여 wave-only + sustained/config-cap binding semantics로 수정했다.

Full technical trail: `docs/test-results/phase4/unit5.2-m3-pending-forensics/` (forensics that
motivated this amendment) and this document.

## 14. Result not overstated

M3 N=400 under the amended rule is GREEN, but the record is: **1 pending-acquire event was
observed in 1 Prometheus scrape during the canonical wave; it was not sustained, and the pool
never reached its configured connection ceiling; no observable effect on completion, reliability,
or client SLO was detected** (`completed=400/400`, `rejected=failed=0`,
`completed_ttfc_p95_s=1.09005`, `completed_duration_p95_s=8.08205`, both within SLO). This is not
reported as "no pending was observed."

## 15. Remaining risk

- The wave-window boundary uses `k6_wall_end_ms` as the upper bound, which includes a small
  (observed: ~150-230ms in the M3 N=400 case) amount of k6-internal teardown time after the last
  VU's actual terminal — a deliberate choice to avoid inventing a tighter, unverified boundary
  (§2), but it means the wave window is very slightly wider than the literal set of client
  iterations. This did not affect any of the 12 re-evaluated points' classification.
- `SUSTAINED_PENDING_MIN_CONSECUTIVE_SAMPLES=3` and the exact-equality `connection_limit_binding`
  rule are new, freshly-designed thresholds with synthetic coverage but zero real-world positive
  (cap-binding-actually-occurred) examples yet — they have only been exercised by one real
  transient-negative case (M3 N=400) and synthetic fixtures. If a future run produces a genuine
  sustained/near-cap signal, it should be scrutinized carefully rather than assumed correctly
  classified on the first try.
- Canonical epoch promotion (Option A) is a recommendation only; the screening-summary.json /
  brackets for M3 have not been recomputed or promoted, pending approval.

## 16. Unit 5 resume readiness

**Not resumed and not to be resumed by this unit.** M3 N=400 was not retried (only offline
re-evaluated from existing raw evidence), N=640 was not run, no M1/M2 refinement was run, and
`unit5-closed-screening/screening-summary.json` was not modified. Awaiting explicit approval of
(a) the Option A epoch-promotion recommendation and (b) resumption of Unit 5 progression.
