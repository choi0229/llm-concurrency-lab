# Phase 4.1 — Extended Closed Boundary: FINAL

Per `docs/decisions/phase4-scalability-definition.md` §16-3 Option B, explicitly authorized by the
user. Does not modify `docs/test-results/phase4/unit6-closed-formal/` (original Closed Formal,
N<=640) in any way.

## Control extension

`docs/test-results/phase4/unit6.5-extended-closed-control/SUMMARY.md`: robust_clean_ceiling
extended from 800 to **1400** (repeat-verified, ~2-3% variance across two independent runs) ->
**SAFE_CLOSED_MAX_EXTENDED = 1120** (1400/1.25). N=1600 (already known CLEAN_MARGINAL from Unit 3,
2.4x cross-run variance) and N=3200 (already known FAIL/LOADGEN_LIMIT) were not re-tested — their
existing Unit 3 classification stands unchanged.

## M3 pool re-freeze

`EXTENDED_M3_MAX_CONNECTIONS = WEBCLIENT_PENDING_ACQUIRE_MAX_COUNT = 1400` (ceil(1120*1.25)),
applied via a new `scripts/run-phase4-closed-benchmark-extended.sh` (byte-diff-verified identical
to the frozen Unit 4/5/6 harness except this one env value and its own output path) and a new
`scripts/collect_phase4_extended_closed_result.py` (byte-diff-verified identical to the frozen
collector except `M3_FROZEN_WEBCLIENT_MAX_CONNECTIONS=1400` and `SAFE_CLOSED_MAX=1120`). Both
originals are completely untouched. M3 N=20 functional regression: VALID, GREEN,
`connection_limit_evaluable=true` under the new config, confirmed before any scalability run.

**Tooling note (disclosed, not hidden)**: the extended collector was first copied without updating
its inherited `SAFE_CLOSED_MAX=640` constant, causing the very first Extended screening attempt
(M2 N=800) to be wrongly flagged `control_range_ok=False`. This was a screening-tool oversight
caught immediately (before any second point ran), fixed by updating the constant to 1120, and the
already-executed M2 N=800 raw evidence was re-collected (not re-run) and validated correctly — no
Gateway/Mock/k6 production behavior was affected, and no result was discarded or re-run to change
an unwanted outcome.

## Extended Closed Screening (`unit6.6-extended-closed-screening/`)

| N | M2 | M3 |
|---|---|---|
| 800 | GREEN | GREEN |
| 1000 | GREEN | GREEN |
| 1120 (SAFE_CLOSED_MAX_EXTENDED) | GREEN | GREEN |

Both models GREEN through the entire extended control-safe range — **no boundary found**. Per
Case 3 (§A-18): both control-censored, one confirmation cell each for Extended Formal.

## Extended Closed Formal (`unit6.7-extended-closed-formal/`)

- **EF1 (M2, N=1120)**: 3/3 GREEN -> **CONFIRMED SUSTAINABLE**
- **EF2 (M3, N=1120)**: 3/3 GREEN -> **CONFIRMED SUSTAINABLE**

## Final Extended Closed conclusions

- **M2 (Virtual Thread)**: MSC >= 1120, MRC >= 1120, **CONTROL_CENSORED** (extended range; still
  not the architecture's true ceiling).
- **M3 (WebFlux)**: MSC >= 1120, MRC >= 1120, **CONTROL_CENSORED** (extended range; still not the
  architecture's true ceiling).
- **M2 vs M3 exact ceiling ranking: INCONCLUSIVE** (both censored, per §16-4 — never assigned an
  arbitrary order).

## First-limiting-resource observation (diagnostic, not a boundary finding)

Neither model showed any resource approaching a limit at N=1120 — this is an observation of
*headroom*, not a discovered saturation point:

| | M2 (N=1120) | M3 (N=1120) |
|---|---|---|
| platform threads (peak, 3 reps) | 273-293 | 30 (flat, all 3 reps identical) |
| CPU avg cores | 0.134-0.157 | 0.139-0.162 |
| RSS peak | 528-570 MB | 294-344 MB |
| FD peak | 2261 (all 3 reps identical) | 2286 (all 3 reps identical) |

M2's platform-thread count continues to scale sub-linearly with virtual-task count (as at N=640);
M3's platform-thread count remains completely flat regardless of N (event-loop model, unaffected
by concurrency) — both patterns consistent with, and extending, what was already observed at
N<=640 in the original Closed Formal. FD (~2261-2286) is far below the Unit 3 safety threshold.

## Part B transition

No unresolved correctness bug, measurement bug, clock anomaly, host instability, or
Mock/control/source inconsistency. The one tooling issue (stale constant in a freshly-forked
script) was caught and fixed before it affected any real measurement, per the disclosure above.
Per §A-21, "control extension yielded a clean censored result rather than a discovered ceiling" is
explicitly not a Part B blocker. **Proceeding to Part B (Open-model).**
