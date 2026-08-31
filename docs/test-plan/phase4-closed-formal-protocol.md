# Phase 4 Unit 5.5 — Closed Formal Protocol Freeze

Everything in this document is frozen before any Formal load runs. Formal load (Canary or the
18-run matrix) is **not** executed by this document — that is Unit 6.

## 0. Formal selection source

The only valid source for Formal cell selection is the final canonical Unit 5 screening result:

```
screening_epoch = post-timeout-fix-pending-semantics-v2.1
```

(`docs/test-results/phase4/unit5-closed-screening/SUMMARY.md`, PASS/COMPLETE). Pre-timeout-fix
epoch data and pre-pending-amendment classifications are never used, directly or indirectly.

## 1. Deviation from the frozen 27-run structure — documented, not silent

`docs/decisions/phase4-scalability-definition.md` §12/§14 freezes **3 models x 3 levels
(N_LOW/N_MSC/N_OVER) x 3 repeats = 27 canonical runs**, and `docs/test-plan/phase4-design.md`'s
roadmap names Unit 6 "Closed Formal 27 runs." This unit deviates from that literal structure, by
explicit user decision, for a reason the original freeze did not contemplate:

**The 27-run design assumes every model has a discovered N_OVER (a real SLO/reliability failure
point).** M2 and M3 did not produce one within the calibrated control-valid range
(`SAFE_CLOSED_MAX=640`) — both are **control-censored** at N=640, meaning the screening boundary
was still GREEN when measurement had to stop, not that a failure point was found and then
under-explored. Applying "N_LOW/N_MSC/N_OVER x 3 repeats" to a model with no N_OVER has no
well-defined N_OVER level to assign. Rather than inventing a synthetic failure level for M2/M3
(which would mean testing above `SAFE_CLOSED_MAX`, itself frozen and not to be exceeded) or
running a redundant low-point repeat that the primary research question ("how far is each
architecture sustainable") does not need, this unit uses a **6-cell, 18-run** Formal matrix instead:
4 cells for M1 (which has a real, screening-confirmed MSC and MRC boundary) and 1 cell each for
M2/M3 (confirming the control-censored GREEN state holds under repetition, since that — not an
undiscovered failure point — is what Unit 5 actually found for them).

This is recorded here as the explicit, disclosed reconciliation of the conflict, per user
decision, not as a silent override of the frozen 27-run text.

## 2. Formal Confirmation Rule — reused verbatim from the existing freeze

`docs/decisions/phase4-scalability-definition.md` §14, applied unchanged:

- **3/3 boundary-predicate PASS** -> `CONFIRMED` (worded `CONFIRMED SUSTAINABLE` for
  GREEN-expected cells, matching §14's own wording, since "sustainable" is the correct concept
  there; worded plain `CONFIRMED` for boundary-transition cells where "sustainable" doesn't apply)
- **2/3 or 3/3 boundary-predicate FAIL** -> `CONFIRMED UNSTABLE` / `NOT CONFIRMED` (same wording
  split as above)
- **Mixed (2 PASS/1 FAIL)** -> `AMBIGUOUS` -> **at most 2 additional valid repetitions** (5
  total). Under the n=3 rule above, 2 PASS/1 FAIL is the *only* reachable `AMBIGUOUS` seed —
  1 PASS/2 FAIL and 0 PASS/3 FAIL are already `CONFIRMED UNSTABLE` at n=3 (`>=2 FAIL`), and 3
  PASS/0 FAIL is already `CONFIRMED` there. So after the 2 extras, n=5 can only ever land on
  exactly one of three reachable states — 5-0 and 0-5 are mathematically unreachable from that
  seed. **Final n=5 resolution, frozen (Unit 5.5 closure), stated as the literal reachable states,
  not as a description**:

  | n=5 outcome | Result |
  |---|---|
  | 4 PASS / 1 FAIL | `CONFIRMED` (`CONFIRMED SUSTAINABLE` for GREEN-expected cells) |
  | 3 PASS / 2 FAIL | `INCONCLUSIVE RANGE` |
  | 2 PASS / 3 FAIL | `CONFIRMED UNSTABLE` (`NOT CONFIRMED` for boundary-transition cells) |

  No other n=5 outcome is reachable from the frozen n=3 rule. No additional labels are used beyond
  the existing §14 vocabulary (`CONFIRMED SUSTAINABLE`, `CONFIRMED UNSTABLE`, `AMBIGUOUS`,
  `INCONCLUSIVE RANGE`) — `CONFIRMED_WITH_VARIABILITY` and `MIXED` are not used anywhere in this
  protocol.

- **PASS/FAIL, precisely defined**: "PASS" means *the cell's boundary predicate held on that rep*
  (§4's predicate column) — not "the run itself succeeded or failed" (that is VALID/INVALID,
  decided before any predicate is read, per §8). "FAIL" means the predicate did not hold. This
  distinction matters most for F2/F3/F4 (see §4/§6 below).
- No pursuit of a "nicer" result: once a cell reaches `CONFIRMED`/`CONFIRMED UNSTABLE`/`NOT CONFIRMED`
  at 3/3, or `INCONCLUSIVE RANGE` at 5, no further repetition of that cell is permitted.

This rule governs the **boundary predicate** (see §4) — the thing being confirmed is "does the
screening-observed boundary reproduce," not the exact color label. Exact-signature reproduction
(e.g., specifically AMBER, specifically `MODEL_TIMEOUT`) is tracked as a **separate, secondary
tally per §4** and never drives the repeat/extra-run machinery.

## 3. M1 failure-signature freeze — not a queue-capacity boundary

Screening evidence (`m1-n400-screen1/result.json` and the full N=50..400 series): at every point
from N=56 upward, `executor_active_peak` is pinned at exactly 50 (`PT_WORKER_COUNT`), and
`executor_queue_depth_peak` tracks `N-50` exactly (56->6, 62->12, 75->25, 100->50, 200->150,
300->250, 350->300, 400->350) with `executor_rejected_final=0` throughout — the bounded queue
(capacity 500) was never full. The observed reliability boundary at N=400 is:

```
worker saturation (50 active) -> queue wait grows -> 60s absolute Gateway deadline -> MODEL_TIMEOUT
```

**Formal must not describe this as "M1 failed because the queue filled up."** The failure
signature to reproduce in Formal is `MODEL_TIMEOUT` (§4 F4's signature predicate); queue-capacity
rejection (`AbortPolicy`, `executor_rejected>0`) was not observed anywhere in the calibrated
screening range and is not what Formal is confirming.

## 4. Formal cells (frozen, N values fixed, not adjustable post-hoc)

| Cell | Model | N | Role | Boundary predicate | Signature predicate (secondary, non-driving) |
|---|---|---|---|---|---|
| F1 | m1 | 50  | MSC lower endpoint | `color == GREEN` | `color == GREEN` |
| F2 | m1 | 56  | MSC upper endpoint | `color != GREEN` | `color == AMBER` |
| F3 | m1 | 350 | MRC lower endpoint | `reliability_pass == true` | `color == AMBER` |
| F4 | m1 | 400 | MRC upper endpoint | `reliability_pass == false` | `classification == MODEL_TIMEOUT` |
| F5 | m2 | 640 | control-censored lower-bound confirmation | `color == GREEN` | `color == GREEN` |
| F6 | m3 | 640 | control-censored lower-bound confirmation | `color == GREEN` | `color == GREEN` |

F2's boundary predicate is deliberately `color != GREEN` (not `slo_pass == false` conjoined with
`reliability_pass == true`) — it confirms "this point is genuinely past the MSC boundary" (AMBER
or RED both satisfy that), while the AMBER-specific signature is tracked separately. If F2 reproduces
as RED instead of AMBER: the boundary predicate is still satisfied (still non-GREEN, still evidence
the bracket is real) but "AMBER signature reproduced" fails — both facts are recorded, not
collapsed into one pass/fail. Symmetrically for F4: `reliability_pass==false` (any RED) satisfies
the MRC boundary predicate; `classification==MODEL_TIMEOUT` specifically is the separate,
non-driving signature check (§3).

M1 is not run at N=640 (already known-RED at N=400, per Unit 5 screening — re-testing above a
confirmed failure boundary is not informative). M2/M3 are not run at N=50 (already GREEN at every
screened point below their control-censored ceiling; the open research question for them is
whether the N=640 ceiling itself reproduces, not whether a low point is stable — already
established beyond doubt in Screening). Screening N=50 for M2/M3 remains available as
diagnostic/history only, not a Formal cell.

## 5. Formal-confirmed bracket wording (frozen, to prevent overclaiming)

If F1 and F2 both reach `CONFIRMED` (or `CONFIRMED SUSTAINABLE`/generic `CONFIRMED` per §2):

> Formal-confirmed MSC bracket = [50, 56]

Never "MSC = 50" or "MSC = 56." If F3 and F4 both reach `CONFIRMED`:

> Formal-confirmed MRC bracket = [350, 400]

`classification == MODEL_TIMEOUT` reproduction (F4's signature predicate) is reported as a
**separate finding**, not folded into the bracket-confirmation statement.

If F5 reaches `CONFIRMED`:

> Formal-confirmed: MSC >= 640, MRC >= 640, CONTROL_CENSORED (M2)

Same for F6 (M3). **Never** write `MSC = 640` / `MRC = 640` for M2 or M3, and never claim
`M2 ceiling == M3 ceiling` or `M2 MSC = M3 MSC = 640` — the correct statement, even if both F5 and
F6 confirm, is: **both boundaries were not reached within the calibrated control-valid range.**

## 6. Cross-model ranking rule (frozen)

If M1's Formal-confirmed brackets stay near [50,56]/[350,400] and M2/M3 both confirm
control-censored at >=640: it is valid to say M2's and M3's sustainable concurrency exceeds M1's
**within this control range**. It is **not** valid to rank M2 vs. M3 against each other — both
being GREEN at 640 is censoring evidence, not equal-ceiling evidence. M1 (N<=400) vs. M2/M3
(N=640) resource-metric absolute values (CPU/RSS/FD) are never used for a direct "more efficient"
ranking, since the compared loads differ.

## 7. Formal run reuse policy

Screening runs are **never** reused as Formal replicates. Every Formal run (Canary and all 18+
extras) is fresh: fresh Gateway, fresh Mock, fresh Prometheus, fresh k6, per the same per-run
lifecycle Screening used (prewarm=5 -> wave -> terminal -> drain -> collect -> stall check ->
validity -> cleanup). Screening's role was point selection; Formal's role is repeat confirmation.

## 8. Formal run validity / invalid-retry policy (frozen, identical machinery to Unit 5)

The current, FINAL-FROZEN `collect_phase4_closed_result.py` semantics (wave-only M3
connection-pool evaluation, `m3_connection_metrics_evaluable` / `m3_connection_limit_binding_absent`,
etc. — unchanged, see §12) determine VALID/INVALID before any color is read, exactly as in
Screening. INVALID runs never enter a cell's replicate count but are never deleted (raw preserved
under `*-invalid-attemptN` naming, §11 of the driver design below).

**No-retry classes** (identical set to the Unit 5 screening driver's `NO_RETRY_KEYS`):
`environment_stall_false`, `pid_match`, `internal_error_eq_0`, `write_overflow_eq_0`,
`m3_connection_limit_binding_absent`, `m3_connection_metrics_evaluable`, `postflight_clean`,
`control_range_ok`, `prewarm_contamination_ok`. Any of these -> immediate full Formal STOP, report,
no automatic continuation. All other invalid classes get exactly one fresh retry of that same
cell/rep slot; if the retry is also invalid -> Formal STOP.

## 9. Formal environment (identical to Screening preflight)

AC power connected; Docker Desktop OFF; no unrelated heavy workload; native arm64, no Rosetta;
pinned Temurin 21.0.11+10; pinned k6 v1.8.0 + xk6-sse v0.1.11; Mock exact version unchanged; sleep
prevention (`caffeinate -i -w <driver_pid>`); fresh process isolation per run; target ports clean
before starting.

## 10. Formal preflight source provenance (recorded at Formal start, not here — see driver output)

The driver records, at the start of its run: M1/M2/M3 jar SHA256, `BlockingMockLlmRelay.java`
SHA256 (shared M1/M2 source), `collect_phase4_closed_result.py` SHA256,
`check_phase4_stall.py` SHA256, `04-phase4-closed-wave.js` SHA256, `prometheus-phase4.yml` SHA256,
`run-phase4-closed-benchmark.sh` SHA256, and the Formal driver's own SHA256. Formal does not start
if any of these differ from the values recorded at Unit 5's FINAL FREEZE (verified as part of this
unit — see completion report; all confirmed unchanged as of this freeze).

## 11. Canary (frozen)

No pre-existing Phase 4 Canary point was found for Unit 5.5/6 specifically (Phase 2's Canary
precedent is a different phase/environment and not reused here). New selection, frozen now:

**Canary = M3 N=640**, single run, excluded from the 18-run matrix. Rationale: highest calibrated
closed concurrency in this protocol, exercises the M3 connection-pool evaluability gate at the
point with the most FD/connection pressure, and GREEN is the unambiguous expected state (making an
environment anomaly easy to distinguish from a real signal).

**Canary PASS** requires, from the frozen collector's own fields: `valid=true`,
`client_cohort.target_concurrency=640`, `client_cohort.actual_started=640`,
`client_cohort.invariant_ok=true`, `model_outcome.color=="GREEN"`, `stall_check.stall_detected=false`,
`postflight_clean=true`, `implementation_specific.reactor_connection_pool.connection_limit_evaluable=true`,
`implementation_specific.reactor_connection_pool.connection_limit_binding==false`, and the same
host-safety checks as Screening preflight (FD/port/process cleanliness).

**Canary failure -> 18-run matrix does not start.** No automatic Canary retry. Report the failure
and wait for user approval before any further action.

## 12. M3 connection-pool rule (frozen, unchanged from Unit 5.3/5.3.1/5.3.2)

Wave-only window, exact-timestamp alignment (fail-closed on mismatch), series/cardinality/identity
fail-closed for pending, active, and `max_connections`, `sustained_pending` at
`>=3` consecutive wave samples, time-aligned `connection_limit_binding` at `>=3` consecutive
aligned samples where `pending>0 AND active>=max_connections_configured`. If N=640 Formal/Canary
runs show a pending transient (as N=400 screening did), it is judged under this exact frozen rule
— not reinterpreted after seeing the result.

## 13. Formal output path (isolated from Screening, Screening artifacts untouched)

**Documentation-sync correction (Unit 6 orchestration audit, 2026-08-29): the paths below are the
actual driver-generated names**, verified against the real Canary artifact and the driver's
`run_point()` naming (`{model}-n{N}-{label}`, reusing the exact same convention as the Unit 5
screening driver) — not the illustrative `canary-m3-n640` / `f1-m1-n50-rep1` sketch an earlier
draft of this document used. The raw Canary artifact was not renamed/moved to match this
correction; the correction goes the other way (docs updated to match the real, already-canonical
path).

```
docs/test-results/phase4/unit6-closed-formal/
  m3-n640-canary/                         (Canary -- excluded from the 18-run matrix)
  m1-n50-f1-rep1/ ... f1-rep3/ (+ f1-rep4/f1-rep5 if extras needed)
  m1-n56-f2-rep1/ ...
  m1-n350-f3-rep1/ ...
  m1-n400-f4-rep1/ ...
  m2-n640-f5-rep1/ ...
  m3-n640-f6-rep1/ ...
  canary-result.json
  formal-summary.json
  formal-provenance-canary.json           (hashes recorded at Canary time)
  formal-provenance-formal-start.json     (hashes recorded at Formal-matrix-start time -- both
                                            kept, never overwritten, so Canary-time and
                                            Formal-start-time provenance are independently auditable)
  driver-log.txt
```

No Canary directory is ever named like an F6 replicate (`m3-n640-f6-rep*`) and no F6 replicate
directory is ever named like the Canary (`m3-n640-canary`) — the label suffix always disambiguates
them, so there is no path collision or accidental reuse risk despite both being M3 N=640.

Achieved by output isolation only (§14) — the frozen `run-phase4-closed-benchmark.sh` still writes
to its own hardcoded `unit4-closed-harness/` directory as always; the Formal driver relocates the
result afterward via a pure filesystem move, exactly as the Unit 5 screening driver already does
for its own output root. `unit5-closed-screening/` is never read or written by Formal tooling.

## 14. Run-level harness reuse (frozen: reuse, don't rewrite)

Audited: `run-phase4-closed-benchmark.sh` hardcodes its own output directory
(`docs/test-results/phase4/unit4-closed-harness/<model>-n<N>-<label>/`) and takes
`(model, N, label)` as its only positional arguments — it has no awareness of "Screening" vs.
"Formal" and needs none. The Unit 5 screening driver already solved output isolation the same way
(call harness -> `shutil.move()` the result to its own root). The Formal driver reuses this
identical pattern, so workload, prewarm, collector, stall-check, validity, SLO, and metric
semantics are byte-identical between Screening and Formal — only the output location and the
selection/repeat logic differ, and location is not a measurement-semantic change.

## 15. Formal driver (frozen design, implemented — see completion report for path/hashes)

`scripts/run_phase4_closed_formal.py`. Responsibilities: call the frozen run-level harness exactly
as the screening driver does; run the Canary first (separately, not counted); execute the 18-run
initial matrix in the frozen deterministic order (§16); apply the invalid/retry policy (§8); apply
the confirmation rule (§2) per cell; run extra reps only after all 18 initial reps complete, in a
frozen deterministic order (§17), capped at 2 per ambiguous cell; enforce artifact-overwrite
protection (unique label suffixing, matching the screening driver's own collision-avoidance); hard
STOP on any no-retry invalid or unexplained failure. **The Formal driver does not recompute
GREEN/AMBER/RED itself** — it reads `model_outcome.color`/`classification`/`reliability_pass` etc.
verbatim from the frozen collector's `result.json`.

### 15.1 Orchestration audit finding and fix (2026-08-29, before the 18-run matrix started)

The Canary (`--canary-only`) was run and PASSed exactly once (§11). Before starting the 18-run
matrix, a read-only audit of `main()`'s control flow found that plain invocation called
`run_canary()` unconditionally before the matrix — meaning a normal Formal run would have silently
re-executed a fresh M3 N=640 Canary load, discarding the already-PASSed one, before this issue was
caught. Fixed with three explicit, disclosed modes, none of which touch measurement semantics:

- `--canary-only`: runs Canary exactly once, never starts the matrix (unchanged from §11).
- `--formal-only`: **never calls `run_canary()`**. Fail-closed verifies the existing
  `canary-result.json` (`pass=true`, every recorded check `true`, `result.model`/`result.concurrency`
  matching the frozen Canary cell exactly) before proceeding to the matrix; if that verification
  fails for any reason (missing file, `pass=false`, any check false, wrong model/N), it STOPs
  without starting the matrix and without running a fresh Canary.
- Plain invocation (no flag): reuses an existing verified-PASS Canary the same way `--formal-only`
  does if one is present; only runs a fresh Canary if none exists at all (a truly from-scratch
  environment). This mode is therefore also safe to invoke after a Canary has already passed.

`record_provenance()` was changed to take an explicit filename so the Canary-time and
Formal-start-time provenance snapshots are both preserved as separate files (§13) rather than one
overwriting the other. Formal execution for this unit uses `--formal-only` against the
already-PASSed Canary recorded in §11 — no second Canary load was run.

Driver synthetic coverage added for this fix (19 new checks): `--canary-only` never touches the
matrix; `--formal-only` with a valid existing Canary never calls `run_canary()` and does start the
matrix; `--formal-only` with a missing/failed/wrong-cell Canary artifact never calls `run_canary()`
and never starts the matrix; default mode reuses an existing Canary and only runs a fresh one when
none exists. All existing confirmation-rule and invalid/retry tests continue to pass unchanged.

## 16. Initial 18-run deterministic order (frozen, literal, no reordering after start)

```
Rep block 1: F1, F5, F2, F6, F3, F4
Rep block 2: F6, F3, F1, F4, F5, F2
Rep block 3: F2, F4, F6, F1, F3, F5
```

Each cell appears exactly once per block (3 blocks -> exactly 3 reps/cell, 18 total). This is the
literal order proposed and frozen; not reordered once Formal starts.

## 17. Extra (ambiguous-resolution) run order (frozen)

Extras run **only after all 18 initial reps complete**, never interleaved mid-matrix (to avoid
concentrating time/order bias). If more than one cell is `AMBIGUOUS`, extras are scheduled in cell-ID
order (F1 before F2 before ... F6), each ambiguous cell's own 2 extras run back-to-back
(rep4 then rep5) before moving to the next ambiguous cell. Maximum 2 extra reps per cell, ever.

## 18. Formal tooling scope (frozen)

This unit may implement: the Formal driver, the Formal aggregation script, their synthetic tests
(no real load), and an output-path wrapper. It may **not** modify Gateway production source, Mock
source, k6 workload semantics, collector semantics, SLO thresholds, or any Unit 5 screening result.
Confirmed: zero changes to any of those since Unit 5's FINAL FREEZE (see completion report hashes).

## 19. Formal aggregation script (frozen design)

`scripts/aggregate_phase4_closed_formal.py`. Reads only `unit6-closed-formal/` canonical Formal
runs (Canary and Screening explicitly excluded, pre-fix history excluded). Computes, per cell:
valid run count, GREEN/AMBER/RED counts, boundary-predicate pass count, signature-predicate pass
count (secondary), confirmation state (§2), and failure-classification distribution. Resource
metrics (platform threads, CPU, RSS, FD, TTFC, duration, and model-specific fields per §15 of the
user's brief) are aggregated as **median/min/max/CV** across a cell's valid reps — small-sample
size (3, or up to 5) is always stated alongside these numbers. **Categorical state
(GREEN/AMBER/RED) is never averaged** — only replicate counts and the confirmation-rule state
represent it.

## 20. Freeze declaration

As of this document: Formal cell matrix, confirmation rule, Canary, deterministic run order (18 +
extras), invalid/retry policy, output path, driver/aggregator design, and environment requirements
are **frozen**. Unit 6 executes against this document without changing any of it; a discovered
correctness/measurement problem during Unit 6 is an immediate STOP and report, not an in-flight
edit to this freeze.
