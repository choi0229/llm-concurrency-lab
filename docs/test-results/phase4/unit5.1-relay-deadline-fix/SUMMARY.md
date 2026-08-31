# Phase 4 Unit 5.1 — Shared Blocking Relay Deadline Race Fix — Completion Report

Status: **fix complete, verified, and evidenced. Unit 5 screening restart NOT executed — awaiting
explicit user approval per closing instruction of the Unit 5.1 brief.**

## 1. Exact root cause

`BlockingMockLlmRelay.relay()` (shared, byte-identical between M1 and M2) read SSE chunks in a
loop. Each iteration called `lifecycle.isPastDeadline()` and, if true, simply `break`-ed out of the
loop. This check races `DeadlineWatchdog`'s own scheduled callback, which is the thing that
actually calls `lifecycle.tryTerminate(Outcome.TIMEOUT)`. The relay's own deadline check could
observe "deadline has passed" nanoseconds *before* the watchdog thread had run its callback, so at
the moment of `break`, `lifecycle.isTerminal()` was still `false`. Control then fell through
unconditionally to:

```java
if (!lifecycle.isTerminal()) {
    channel.markProducerDone();
}
```

which is the **normal end-of-stream / COMPLETED path**. A stream that was actually abandoned at its
deadline was therefore misclassified as a normal completion once the write channel's buffer
drained afterward.

Confirmed via triangulating three independent observers on the real M1 N=400 screening run
(`docs/test-results/phase4/unit5-closed-screening-pre-timeout-fix/m1-n400-screen1/`):
client-side k6 showed `completed=350, failed_mid_stream=50`; the Mock LLM's own
`cancelled_requests_total=50`; the Gateway's own server-outcome delta showed
`completed=399` (i.e. counted 49 of those 50 abandoned streams as completed) — the discrepancy
is exactly the deadline-race window width times request volume, and `prewarm_contamination_ok`
failed as the tell.

## 2. Exact source fix

`BlockingMockLlmRelay.java` (M1 and M2, byte-identical — see item 8), in the `isPastDeadline()`
branch: instead of an unconditional `break`, the relay now contests the terminal CAS itself before
breaking:

```java
if (lifecycle.isPastDeadline()) {
    if (lifecycle.tryTerminate(Outcome.TIMEOUT)) {
        channel.terminateNow(Outcome.TIMEOUT);
    }
    break;
}
```

Whichever of {relay, watchdog} wins the CAS is irrelevant — both produce the same `Outcome.TIMEOUT`
exactly once via `RequestLifecycle`'s existing idempotent `tryTerminate`. What changed is that
`isTerminal()` is now **guaranteed true** by the time control reaches the post-loop check, so the
COMPLETED fallthrough can never fire for a stream abandoned at its deadline. No other line in the
method changed. The pre-existing top-of-loop `isTerminal()` guard, the `IOException`/`Exception`
handlers, and the `finally` cleanup block are all untouched.

## 3. How duplicated timeout logic was handled

Considered deduplicating the relay's and `DeadlineWatchdog`'s timeout-termination call into a
shared idempotent helper. Decided against it: `RequestLifecycle.tryTerminate` is already the single
idempotent chokepoint both call sites go through — the two call sites (`relay.tryTerminate(TIMEOUT)`
+ `channel.terminateNow(TIMEOUT)` on win) are already a two-line pattern, and extracting a helper
for a two-line, two-call-site pattern would be a larger diff than the bug fix itself for no
behavioral gain. Kept the fix to the smallest possible change, per the brief's instruction.

## 4. Race regression test description

New file `BlockingMockLlmRelayDeadlineRaceTest.java` (added identically to M1 and M2 test trees),
using a JDK built-in `com.sun.net.httpserver.HttpServer` fixture (no new dependency, no real Mock
LLM/Gateway process) so races are deterministic and fast:

- **Test A** `deadlineObservedBeforeWatchdogMustNotCompleteStream` — isolates the relay's *own*
  deadline-observation path: no `DeadlineWatchdog` armed at all (150ms lifecycle deadline, fixture
  streams continuously every 10ms), so the only way the lifecycle can become terminal is the
  relay's own `isPastDeadline()` check. Covers race scenario **(A)**.
- **Test B** `terminalWonByAnotherPathWhileRelayStillReadingIsRespectedExactlyOnce` — simulates a
  competing winner (stands in for the watchdog, or a disconnect path) firing 30ms into a stream
  with a generous 300ms lifecycle deadline, while the relay is still actively reading. Covers race
  scenarios **(B)** and **(C)**-equivalent (a path other than the relay's own deadline check wins
  first, and the relay must respect it without overwriting).
- **Test C** `normalCompletionStillWorksUnaffectedByTheFix` — sanity control: no deadline pressure,
  stream reaches a genuine `final` event. Covers scenario **(E)**.

Scenario **(D)** (upstream error wins first) is already covered by the pre-existing, unmodified
`catch (IOException e)` handler, which was not touched by this fix and continues to call
`tryTerminate(Outcome.UPSTREAM_ERROR)` through the same idempotent CAS — no new test needed since
the code path is unchanged; scenario (C)'s literal "client disconnect" variant is likewise
unmodified code (governed by the same top-of-loop `isTerminal()` guard exercised in Test B).

## 5. Exactly-once test results

All 3 tests in `BlockingMockLlmRelayDeadlineRaceTest` **PASS** post-fix (both M1 and M2 copies).
**Fail-before/pass-after explicitly verified**: the fix was temporarily reverted and Test A was
re-run — it failed with `Outcome.COMPLETED` where `Outcome.TIMEOUT` was expected, confirming the
test actually exercises the bug. Re-applying the fix made all 3 tests pass again. No test observed
a double terminal outcome (COMPLETED+TIMEOUT) in any run.

## 6. M1 full test suite results

`gateway-phase4-platform-queue`: **7/7 tests PASS, 0 failures, 0 errors, 0 skipped** —
`BlockingMockLlmRelayDeadlineRaceTest` (3), `WriteChannelTest` (2), `PlatformTaskSubmitterTest` (2).
JUnit XML preserved at `unit5.1-relay-deadline-fix/unit-test-results/m1/`.

## 7. M2 full test suite results

`gateway-phase4-virtual-thread`: **6/6 tests PASS, 0 failures, 0 errors, 0 skipped** —
`BlockingMockLlmRelayDeadlineRaceTest` (3), `WriteChannelTest` (2), `VirtualTaskSubmitterTest` (1).
JUnit XML preserved at `unit5.1-relay-deadline-fix/unit-test-results/m2/`.

## 8. M3-unchanged evidence

`git status --porcelain -- gateway-phase4-webflux` shows the module only as a new untracked
directory (`?? gateway-phase4-webflux/`) with no modification markers — consistent with every other
Phase 4 module never having been committed yet on this branch (`phase3`), and confirms nothing in
it was edited during Unit 5.1. M3 uses Reactor's own timeout operator, not `RequestLifecycle`/
`BlockingMockLlmRelay`/`DeadlineWatchdog`, so it was never in scope for this fix. M1/M2 `common`
package parity independently confirmed: `diff -rq gateway-phase4-platform-queue/.../common
gateway-phase4-virtual-thread/.../common` → **identical, no output**.

## 9. M1 functional timeout result

Targeted functional smoke F1-F4 run against a real Mock LLM + real M1 Gateway process (not the
deterministic unit test — this is the process-level smoke required by §12). F2 (short
absolute-timeout scenario): outcome recorded as **TIMEOUT**, not COMPLETED. F1 (normal SSE), F3
(client disconnect), F4 (upstream failure) all classified correctly. Logs at
`unit5.1-relay-deadline-fix/functional-smoke-logs/m1-f1-f2-f3.log` and `m1-f4.log`.

## 10. M2 functional timeout result

Same F1-F4 smoke run against M2. F2 outcome: **TIMEOUT**, not COMPLETED. F1/F3/F4 correct. Logs at
`functional-smoke-logs/m2-f1-f2-f3.log` and `m2-f4.log`.

## 11. M1 N=20 regression (frozen harness, unmodified)

`docs/test-results/phase4/unit5.1-relay-deadline-fix/m1-n20-unit51-regression/`:
`valid=true`, `color=GREEN`, `client_cohort={target=20, actual_started=20, target_reached=true,
completed=20, rejected=0, failed_mid_stream=0, failed_no_event=0, terminal_sum=20,
invariant_ok=true, dropped_iterations=0}`, `validity.checks.prewarm_contamination_ok=true`,
`postflight_clean=true`, Mock `completed_final=25` (5 prewarm + 20 wave, consistent with the
prewarm-correction convention). This is regression evidence only, not a scalability result.

## 12. M2 N=20 regression (frozen harness, unmodified)

`m2-n20-unit51-regression/`: identical shape — `valid=true`, `color=GREEN`, cohort
`{20/20 completed, all others 0, invariant_ok=true}`, `prewarm_contamination_ok=true`,
`postflight_clean=true`, Mock `completed_final=25`.

## 13. Measurement harness diff: confirmed zero

```
git diff --stat -- scripts/run-phase4-closed-benchmark.sh scripts/collect_phase4_closed_result.py \
  scripts/check_phase4_stall.py load-test-k6/scenarios/04-phase4-closed-wave.js \
  monitoring/prometheus/prometheus-phase4.yml
```
produced no output — all 5 frozen files are byte-identical to their Unit 4.6-frozen state. (They
are untracked-new on this branch like the rest of Phase 4, so "diff" here is verified by the
absence of any edit since their first-write in this working tree, not a tracked-file diff; no
`.orig`/backup comparison was needed since no edits were ever made to them in Unit 5.1.)

## 14. Screening driver test results

New `scripts/test_phase4_screening_driver_synthetic.py` — **23/23 checks PASS**, zero real
Gateway/Mock/k6/subprocess calls. Covers: GREEN→GREEN, GREEN→AMBER (normal degradation, must NOT
flag), GREEN→RED, AMBER→RED (normal degradation, must NOT flag), AMBER→GREEN (non-monotonic, must
flag — verified flagged), RED→GREEN (non-monotonic, must flag — verified flagged), bracket
computation (`green_high`/`non_green_low`/`reliability_high`/`red_low`/`red_classification`),
N=640 GREEN control-censoring (both MSC and MRC censored), N=640 AMBER (MRC censored, MSC bracket
still refinable — NOT censored), known-RED (`MODEL_REJECTION`) deactivating only that model without
triggering the global STOP, unexplained failure (`UNCLASSIFIED_MODEL_FAILURE`) triggering the
global STOP, one-retry-then-success for a recoverable invalid reason, repeated-invalid triggering
STOP after exactly one retry, and no-retry-class invalid triggering STOP immediately with zero
retries. This exercises exactly the inverted-comparison bug class that was caught and fixed
mid-Unit-5 (`AMBER(50)→GREEN(100)` and `RED(100)→GREEN(200)` are the two cases the brief named
explicitly).

## 15. How pre-fix artifacts were preserved

`docs/test-results/phase4/unit5-closed-screening/` was renamed (not copied, not deleted) to
`unit5-closed-screening-pre-timeout-fix/`. Nothing inside was edited except the addition of two new
files: `EPOCH-README.md` (marks `screening_epoch=pre-timeout-race-fix`, tabulates every point's
disposition, states explicitly that none of these — including the ones that never exhibited the bug
— are canonical post-fix evidence) and `m1-n400-screen1/INVALID_MEASUREMENT_DUE_TO_GATEWAY_CORRECTNESS_BUG.md`
(marks the N=400 run as `INVALID_MEASUREMENT_DUE_TO_GATEWAY_CORRECTNESS_BUG`, explicitly not
`MODEL_TIMEOUT`/`MODEL_REJECTION`/any MSC/MRC evidence). The driver's own non-monotonic-logic bug
and its fix/resume history is preserved as-is in that same directory's `driver-log.txt` and
`screening-summary.json`, referenced from `EPOCH-README.md` rather than hidden.

## 16. How pre-fix canonical exclusion was implemented

Directory-rename + `EPOCH-README.md` metadata marking (the brief's manifest/SUMMARY-level option),
not per-point deletion. No physical move was needed for the frozen harness's own hardcoded output
path (`unit4-closed-harness/`) since Unit 5.1's only harness runs there were the two N=20 regression
runs, which were relocated into `unit5.1-relay-deadline-fix/` immediately after collection —
`unit4-closed-harness/` was confirmed restored to exactly its original Unit 4 contents afterward.

## 17. Unit 5 restart strategy

**Option 1 (user-recommended, adopted)**: when approved, all three models (M1, M2, M3) restart
screening fresh from N=50 in a brand-new epoch under the previously-approved geometric/interleave
plan (`SAFE_CLOSED_MAX=640`, frozen SLO unchanged), writing to a fresh
`docs/test-results/phase4/unit5-closed-screening/` directory (the name freed up by the item-15
rename) with `screening_epoch=post-timeout-race-fix` metadata. Termed a **POST-FIX SCREENING
RESTART**, not a resume. Pre-fix M1/M2 points (even non-bug-exhibiting N=50/100/200 ones) and
pre-fix AMBER observations are excluded from every canonical post-fix bracket calculation — only
new post-fix measurements count, per the brief's explicit rule that the fix can affect terminal
semantics/cleanup timing broadly, not just at N=400.

## 18. Discovered risks

- The deadline-race window is timing-dependent; it manifested at N=400 for M1 but was not observed
  to manifest at N≤200 in the pre-fix epoch for either M1 or M2 — this is exactly why the brief's
  rule against reusing *any* pre-fix M1/M2 points (item 14/20 of the original brief) matters: the
  absence of an observed symptom at low N is not evidence of the bug's absence, only of the race
  window not being hit in that particular run.
- `DeadlineWatchdog`'s own callback path was exercised indirectly (Test B simulates a generic
  competing winner rather than driving the real `DeadlineWatchdog` class through its actual
  scheduler); this is standing behavior unchanged by the fix and was not the object of Unit 5.1, but
  is flagged here for completeness in case a future Unit wants a `DeadlineWatchdog`-specific test.
- No new risk was introduced to M3 (untouched) or to non-timeout outcome paths (upstream error,
  disconnect, internal error) — all governed by pre-existing, unmodified code exercised by the
  existing (unchanged) `WriteChannelTest` and by this Unit's functional F1/F3/F4 smokes.

## 19. Unit 5 post-fix restart readiness: **PASS**

All 21 checklist items from the Unit 5.1 brief are satisfied:
background waiters confirmed via actual PID and cleaned up; pre-fix artifacts preserved via rename
+ annotation; root cause documented (this file, §1); fix applied (§2); M1/M2 parity maintained
(byte-identical `common`, §8); M3 unchanged (§8); deterministic race regression passing for both
relay-first and other-path-first races with exactly-once outcomes (§4-5); normal completion test
passing (§4); full M1 tests passing 7/7 (§6); full M2 tests passing 6/6 (§7); targeted M1/M2
functional timeout smokes passing (§9-10); M1/M2 N=20 harness regression passing GREEN/clean
(§11-12); frozen measurement harness unchanged, zero diff (§13); screening-driver synthetic tests
passing 23/23 (§14); pre-fix M1/M2 points marked canonically excluded (§15-16, §20 of original
brief). Environment is clean (no stray processes, all target ports free) and `git status` shows only
expected new Phase 4 files, nothing unintended.

**Per the user's explicit closing instruction, Unit 5 screening has NOT been restarted and no
N=50 run has been executed. This report is submitted for review; the post-fix screening restart
will begin only after explicit approval.**
