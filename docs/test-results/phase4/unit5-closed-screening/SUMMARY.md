# Phase 4 Unit 5 — Closed-model Scalability Screening
## FINAL canonical epoch: post-timeout-fix-pending-semantics-v2.1 — PASS / COMPLETE

**screening_epoch = post-timeout-fix-pending-semantics-v2.1** (current, final, canonical — the
only epoch this document reports against)

Status: **PASS / COMPLETE. No STOP triggered under the final semantics.** This is a screening
result (MSC/MRC brackets), not a Formal benchmark claim.

## Canonical dataset

**19 canonical raw screening points -> 19/19 valid under FINAL semantics**
(`post-timeout-fix-pending-semantics-v2.1`): 12 initial geometric (N=50/100/200/400 x 3 models) +
M2 N=640 + M3 N=640 + 5 M1 refinement points (N=75, 62, 56 for MSC; N=300, 350 for MRC). Retries: 0.

## Historical note (superseded epochs and classifications — not current state)

**Epoch history.** The very first screening epoch's M1 N=400 high-load path exposed a shared
blocking-relay deadline race (Unit 5.1); that entire epoch
(`unit5-closed-screening-pre-timeout-fix/`) was excluded from canonical calculation and is never
used here. Following the fix and regression, all three models were re-collected from N=50 under
an epoch then labeled `post-timeout-race-fix` — this label is now superseded and appears in this
document only as history, never as the current epoch.

**Classification history for M3 N=400.** Under that now-superseded epoch's original
`pending_peak>0` validity rule, M3 N=400 was flagged INVALID. Offline forensics (Unit 5.2) showed
this was a transient connection-pool ramp-up sample uncorrelated with any client-visible effect.
Three amendment passes (Unit 5.3, 5.3.1, 5.3.2) replaced that rule with the current, final,
time-aligned, evaluability-gated `connection_limit_binding` check. Under that final rule, all 12
pre-resume raw points were re-evaluated offline (no new load): 11 were unchanged and M3 N=400
reclassified VALID+GREEN. **This is the current, final classification for M3 N=400 — the dataset
does not contain an invalid point.** The pre-amendment classification is historical only, and its
raw evidence is preserved (not deleted) at `m3-n400-screen1/result-preclosure-original.json` for
audit purposes. Pre-fix (first-epoch) numbers are never mixed into any table in this document.

## Geometric + refinement tables

| N | M1 | M2 | M3 |
|---|---|---|---|
| 50 | GREEN | GREEN | GREEN |
| 56 (MSC refine) | AMBER | - | - |
| 62 (MSC refine) | AMBER | - | - |
| 75 (MSC refine) | AMBER | - | - |
| 100 | AMBER | GREEN | GREEN |
| 200 | AMBER | GREEN | GREEN |
| 300 (MRC refine) | AMBER | - | - |
| 350 (MRC refine) | AMBER | - | - |
| 400 | RED (MODEL_TIMEOUT) | GREEN | GREEN |
| 640 | not run (already RED at 400) | GREEN | GREEN |

## Screening brackets

- **M1 MSC bracket: [50, 56]** (width 12% <= 20% stop rule; 3 refinement points: 75, 62, 56)
- **M1 MRC bracket: [350, 400]** (width 14.3% <= 20% stop rule; 2 refinement points: 300, 350)
- **M1: not control-censored** — a real RED boundary (MODEL_TIMEOUT) was found at N=400.
- **M2 MSC: control-censored, MSC >= 640.** **M2 MRC: control-censored, MRC >= 640.** (N=640 GREEN,
  no refinement applicable.)
- **M3 MSC: control-censored, MSC >= 640.** **M3 MRC: control-censored, MRC >= 640.** (N=640 GREEN,
  same as M2.)

No model showed SCREENING_NON_MONOTONIC. No unexplained-failure classification occurred in the
resume phase.

## M1 signature (executor/queue/timeout)

`executor_active_peak` pinned at exactly 50 (=PT_WORKER_COUNT) from N=56 upward; `executor_queue_depth_peak`
tracks `N-50` exactly at every point (56->6, 62->12, 75->25, 100->50, 200->150, 300->250, 350->300,
400->350); `executor_rejected_final=0` throughout (queue capacity 500 never reached). At N=400:
`completed=350, timeout=50` (`MODEL_TIMEOUT`, 0 misclassified as completed — the Unit 5.1 fix
validated under real load).

## M2 signature (virtual-thread/platform-thread)

`virtual_tasks_active_peak` == N at every point (50/100/200/400/640); at N=640:
`virtual_tasks_active_peak=640`, `virtual_tasks_started_final=645` (640+5 prewarm),
`platform_threads_peak=218` (far below the 640 virtual-task count), `avg_cores_estimate=0.11`,
`rss_peak_kib=404576`, `fd_peak=1301`. `completed=640/640`, SLO PASS throughout.

## M3 signature (connection pool)

`reactor_connection_active_peak` == N at every point; at N=640: `active_peak_wave=640`,
`pending_peak_wave=0` (no acquire contention at all at max screening load),
`connection_limit_evaluable=true`, `platform_threads_peak=30` (flat across all N, WebFlux
event-loop independence from concurrency), `avg_cores_estimate=0.106`, `rss_peak_kib=265312`,
`fd_peak=1326`. `completed=640/640`, SLO PASS throughout.

## Transient/sustained pending diagnostics (M3, all points)

| N | pending_peak_wave | sustained_pending | connection_limit_binding |
|---|---|---|---|
| 50/100/200 | 0 | False | False |
| 400 | 1 (1 scrape, wave-start ramp-up transient, Unit 5.2 forensics) | False | False |
| 640 | 0 | False | False |

No model ever showed a sustained or configured-cap-binding pending signal.

## CPU / RSS / FD across models (peak, N=640 or highest run point)

| Model | Platform threads peak | CPU (avg cores est.) | RSS peak (KiB) | FD peak |
|---|---|---|---|---|
| M1 (N=400, highest run) | 216 | 0.06 | 324192 | 469 |
| M2 (N=640) | 218 | 0.11 | 404576 | 1301 |
| M3 (N=640) | 30 | 0.106 | 265312 | 1326 |

All well within Unit 3 host safety limits; no EMFILE/EADDRNOTAVAIL/FD-pressure observed anywhere.

## Client / Gateway / Mock cross-observer consistency

Every valid point (all 19 canonical runs): `mock completed_final == client completed + 5`
(prewarm), `gateway_active_requests_final == 0`, `mock active_final == 0` at drain. No
unexplained divergence anywhere in the resume phase (M2 N=640, M3 N=640, all 5 M1 refinement
points individually verified). Start spread: 22ms (M2 N=640), 29ms (M3 N=640) — both far under
the 200ms threshold.

## Frozen collector/harness changes

`run-phase4-closed-benchmark.sh`, `check_phase4_stall.py`, `04-phase4-closed-wave.js`,
`prometheus-phase4.yml`: **0 changes** across the entire Unit 5 (mtimes predate Unit 5.1
completion). Gateway/Mock/k6 source: **0 changes** since Unit 5.1. `collect_phase4_closed_result.py`
and `run_phase4_closed_screening.py` were amended three times (Unit 5.3, 5.3.1, 5.3.2) for the M3
pending-validity semantics only — declared **UNIT 5 CLOSED SCREENING FINAL FREEZE** before this
resume; no further change was made during the resume itself.

## Unit 5.5 readiness

**PASS for screening purposes.** M1 has a real RED boundary with narrow MSC/MRC brackets; M2/M3
are both control-censored at SAFE_CLOSED_MAX=640 with zero degradation signal at the ceiling. No
Formal claim is made here (per instruction) — MSC/MRC are reported as screening brackets, not
confirmed capacities. Unit 5.5 (Closed Formal Protocol Freeze) was **not started** by this unit.
