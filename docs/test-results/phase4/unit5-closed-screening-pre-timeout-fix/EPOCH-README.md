# Unit 5 Screening — PRE-TIMEOUT-FIX EPOCH (superseded, preserved as history)

**`screening_epoch = pre-timeout-race-fix`**. Every artifact in this directory was collected
against the Gateway source revision **before** the `BlockingMockLlmRelay` deadline-race fix
(Unit 5.1). None of these points are used as canonical Screening MSC/MRC evidence in the Phase 4
Final Report, **even the ones that did not directly exhibit the bug** (M1 N=50/100/200, all of
M2, all of M3) — the experimental unit for Phase 4 is a fixed source/binary revision, and this
revision changed. Preserved for provenance/history only; see `docs/test-results/phase4/
unit5-closed-screening/` for the post-fix canonical dataset.

| Model | N | Pre-fix color | Disposition |
|---|---|---|---|
| M1 | 50 | GREEN | superseded — re-measure post-fix |
| M1 | 100 | AMBER | superseded — re-measure post-fix |
| M1 | 200 | AMBER | superseded — re-measure post-fix |
| M1 | 400 | INVALID (see `m1-n400-screen1/INVALID_MEASUREMENT_DUE_TO_GATEWAY_CORRECTNESS_BUG.md`) | root cause of the fix — NOT MODEL_TIMEOUT/MODEL_REJECTION evidence |
| M2 | 50 | GREEN | superseded — re-measure post-fix (shares the buggy relay code, even though not observed to trigger it in this range) |
| M2 | 100 | GREEN | superseded — re-measure post-fix |
| M2 | 200 | GREEN | superseded — re-measure post-fix |
| M3 | 50 | GREEN | superseded — re-measure post-fix (M3 source itself is unchanged, but re-measured anyway for single-epoch provenance simplicity, per the approved Option 1) |
| M3 | 100 | GREEN | superseded — re-measure post-fix |
| M3 | 200 | GREEN | superseded — re-measure post-fix |

Also preserved here: `driver-log.txt` and `screening-summary.json` from this epoch's run, including
the record of the screening driver's own non-monotonic-comparison bug (found and fixed mid-epoch,
before it could affect any capacity conclusion — see Unit 5.1 report section on driver history).
