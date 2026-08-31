# Phase 4 Unit 6.1 — F6-rep3 Workload Pacing Integrity Forensics

No new load was generated. Pure offline, read-only analysis of existing F6-rep1/rep2/rep3, Canary,
and F5-rep1/2/3 raw artifacts, plus source-code audit of `mock-llm-fastapi/app/main.py` and
`gateway-phase4-webflux/.../ChatHandler.java`. No raw artifact was modified.

## Current Phase 4 Unit 6 status

**Formal execution complete BUT Formal integrity review pending.** Not yet confirmed:

- F6 3/3 final confirmation
- M3 Formal-confirmed MSC>=640 / MRC>=640
- Phase 4 Closed Formal FINAL PASS
- Final Report
- Open-model test

F1-F5 raw/result artifacts are untouched by this unit.

## Verdict: Case C — Environment/clock anomaly confirmed

Full evidence trail in `mock-pacing-source-audit.md`, `workload-provenance.md`,
`clock-audit.md`, `event-count-audit.md`, `cross-run-timing.csv`. Summary of the decisive chain:

1. **Mock's pacing algorithm cannot complete early** (`asyncio.sleep()` is a hard floor, never an
   early-return) — rules out Case B on code-level grounds alone.
2. **No workload/config parameter difference found** — harness hardcodes 1000/200/35
   unconditionally, Gateway is a byte-for-byte JSON passthrough (never touches these fields),
   event count (23220 = 645*36) and Mock-logged `firstChunkDelayMs=1000` are byte-identical across
   F6-rep1/rep2/rep3 — rules out Case A.
3. **Mock's own monotonic-clock-based internal histogram
   (`mockllm_stream_duration_seconds`) shows F6-rep3 averaging 8.098s** — statistically
   indistinguishable from F6-rep1 (8.079s), F6-rep2 (8.004s), and Canary (7.902s). The actual,
   physical, monotonic elapsed processing time for every one of F6-rep3's 640 requests was
   completely normal.
4. Only the **wall-clock-based** measurements — k6's `Date.now()`-based
   `client_completed_stream_duration_seconds` (6.094-6.347s), Mock's `logging`-timestamped
   "stream requested"/"completed" lines (~6.1-6.3s), and even the bash-harness's own outer
   `time.time()`-based k6-process wall span (46.073s vs. 47.7-48.0s for its siblings, a matching
   ~1.85s compression) — show the anomaly, and all three independently-clocked wall-clock
   observation points show it by roughly the same ~1.7-2.0s magnitude, uniquely in the F6-rep3
   window.

This is the signature of a **host wall-clock adjustment (e.g., an NTP correction or system time
sync) occurring during F6-rep3's ~46-second run window** — not a genuine change in Mock's
processing speed, not a workload/config difference, and not a per-measurement glitch (three
independent wall-clock-based layers agree). Direct host NTP/system-clock logs were not consulted
(out of scope, no new instrumentation added); the conclusion rests on cross-metric triangulation
against the one clock source immune to the effect (Mock's internal monotonic histogram), which is
treated as sufficient given it is direct proof, not inference.

## 150ms-chunkInterval hypothesis: rejected

`1000 + 34*150 = 6100`ms is numerically close to the observed wall-clock duration, but Mock's own
monotonic `first_chunk_latency` (1.0011s, matching siblings) and `stream_duration` (8.098s,
matching siblings) histograms directly disprove any actual shortened interval was used. The
numeric proximity is coincidental.

## Additional under-duration runs: none found

`cross-run-timing.csv` covers all 18 Formal runs + Canary. Every run's population minimum
completed-stream duration is >=7.87s except F6-rep3 (6.09-6.35s, its *entire* population, not a
tail effect). F5 (M2 N=640, same Mock workload, 3 reps) shows no compression at all
(7.958-8.280s range). This is scoped to F6-rep3 alone — not a broader Formal-wide STOP condition
per the governing instruction's escalation rule (§18), since no second occurrence was found.

## F6-rep3 current validity: WITHDRAWN as a canonical Formal replicate

The underlying **model behavior** recorded for F6-rep3 (640/640 completed, 0 rejected/failed,
36/36 events per request, `connection_limit_evaluable=true`, `connection_limit_binding=false`) is
verified normal via the monotonic-clock cross-check and is not in question. However, its
**client-side latency measurements are not trustworthy** for this run (wall-clock corrupted for
the duration of the anomaly), and per the governing principle that "same workload" implies "same
trustworthy measurement environment" across replicates — a clock integrity failure is a control
integrity problem, not a model-architecture finding. F6-rep3 is therefore withdrawn as a canonical
Formal replicate (ENVIRONMENT/CONTROL INVALID), its raw artifact preserved unmodified for the
record, and NOT counted toward F6's 3-valid-repetition requirement.

(Note: had the true monotonic-based latency values been used instead, F6-rep3 would still classify
GREEN — 8.098s average duration and ~1.0s TTFC are both far inside the 10.0s/2.0s SLO thresholds.
The anomaly did not flip a classification; it invalidates confidence in the *specific numbers*
recorded for this rep, which is enough to withdraw it from a Formal confirmation whose entire
purpose is precise repeat measurement.)

## F6 confirmation impact

F6 now has only **2 valid replicates** (rep1, rep2), both GREEN, both with fully normal
monotonic-verified timing. Per the frozen n=3 rule, 2 valid reps is **not yet a determination** —
it is neither `CONFIRMED SUSTAINABLE` (needs 3/3) nor `AMBIGUOUS` (needs exactly 3 attempts with a
2-1 split) nor any other defined state. **F6's status reverts from `CONFIRMED SUSTAINABLE` to
PENDING (2/3 valid, 1 replacement needed).** No replacement run has been executed by this unit.

## Formal aggregate impact

F6's resource/latency aggregate (`formal-aggregate.json`) must exclude F6-rep3's numbers from any
canonical calculation once a replacement policy is applied — its `n=3` resource stats for F6
computed by `aggregate_phase4_closed_formal.py` currently include the contaminated rep3 duration
value and should be treated as **provisional/invalid** until F6-rep3 is formally excluded and (if
approved) a replacement collected. No aggregate file has been regenerated by this unit (offline
forensics only, no code run beyond read-only inspection).

## Replacement run: needed, not executed

A fresh F6 replicate (f6-rep4, following the frozen extra-run naming/order convention) is needed
to reach 3 valid replicates for F6. **Not executed in this unit** — per instruction, this requires
explicit user approval of the replacement policy before any new load runs.

## Phase 4 Closed Formal FINAL PASS: not yet possible

Blocked on F6 reaching 3/3 (or the frozen ambiguous/extra-run resolution) with all-verified,
uncontaminated replicates. F1-F5 are unaffected and remain at their prior CONFIRMED states.
