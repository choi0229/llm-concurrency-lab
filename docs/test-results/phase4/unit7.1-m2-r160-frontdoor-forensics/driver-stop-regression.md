# Open Screening Driver — Unexplained-Failure STOP Bug (found + fixed this Unit)

## The bug

`scripts/run_phase4_open_screening.py` defined `KNOWN_RED = {"MODEL_REJECTION", "MODEL_TIMEOUT"}`
but had **no equivalent of the Closed screening driver's `UNEXPLAINED` set at all**
(`scripts/run_phase4_closed_screening.py` lines 36-37:
`UNEXPLAINED = {"UPSTREAM_ERROR_UNCONFIRMED_CAUSE", "UNEXPECTED_CLIENT_DISCONNECT",
"UNCLASSIFIED_MODEL_FAILURE", "INTERNAL_ERROR_BUG"}`, which triggers `trigger_stop()` for the whole
driver). The Open driver's `initial_pass()` only ever checked `entry["classification"] in
KNOWN_RED` — any other RED classification (including `UNCLASSIFIED_MODEL_FAILURE`) fell through
with no action at all, leaving the model `active` and the driver looping on to the next model/rate.

## The incident this exposed

Live, during actual Screening execution: M2 R=160 returned `valid=true, color=RED,
classification=UNCLASSIFIED_MODEL_FAILURE`. Per `INITIAL_LEVELS`, the very next scheduled run at
that same rate level was `m3 R=160` — and the driver started it immediately (log line `[00:26:43]
RUN m3 R=160 label=screen1`), before any investigation happened. Only a manual `kill` of the driver
process (PID 22342) stopped it; the process tree (k6/gateway/mock/prometheus for that in-flight M3
R=160 run) then exited on its own, leaving an incomplete harness artifact at
`docs/test-results/phase4/unit7-open-harness/m3-r160-screen1/` (never moved to
`unit7-open-screening/`, no `result.json` — several `prom-*.json` files are 0 bytes because the
harness's post-run Prometheus query loop was interrupted mid-curl). This artifact is left in place,
untouched, as history; it is not canonical and must not be used (see `SUMMARY.md` §16).

## The fix

Three changes to `scripts/run_phase4_open_screening.py`:

1. Added the `UNEXPLAINED` set (identical to the Closed driver's).
2. `initial_pass()`: after the existing `KNOWN_RED` branch, added
   `elif entry["classification"] in UNEXPLAINED: trigger_stop(...); return` — halts the entire
   driver immediately, not just the one model.
3. `refine()`: added the same check inside the refinement loop. This closes a gap that exists even
   in the Closed driver's own `refine_bracket()` (which has no equivalent check) — deliberately made
   stricter here rather than reproducing that gap.
4. `main()`: the refinement-phase loop (`for model in [...]: refine(model)`) now checks
   `STOP["triggered"]` between each model, not just once before the whole loop — so a stop raised by
   `refine("m1")` correctly prevents `refine("m2")`/`refine("m3")` from starting.

## Verification

`scripts/test_phase4_open_screening_driver_synthetic.py` (new, no subprocess/real load, `run_point`
monkeypatched) — 5 scenarios, all PASS:

1. **The real incident, replayed**: fake M2 R=160 → `UNCLASSIFIED_MODEL_FAILURE`. Asserts
   `STOP["triggered"] is True` and, critically, that `("m3", 160)` and `("m1", 256)` were **never**
   called — i.e., nothing at the same rate level or any later level runs after the unexplained
   failure.
2. **`KNOWN_RED` still behaves as before**: fake M1 goes RED/`MODEL_TIMEOUT` at R=10 — M1 is
   deactivated (no further M1 calls), `STOP` stays `False`, and M2/M3 both progress all the way to
   R=256 — confirms the fix does not over-trigger and block unrelated models on ordinary known-RED
   findings.
3. **A second `UNEXPLAINED` classification** (`UNEXPECTED_CLIENT_DISCONNECT`) also halts everything
   at a different rate/model to confirm the check isn't hard-coded to the one classification string.
4. **Existing no-retry-INVALID behavior unaffected**: an environment/control INVALID
   (`clock_integrity_ok`) still halts the whole driver as before (this path already existed; the
   test pins that the new code didn't regress it).
5. **Refinement-phase stop**: a fake `UNCLASSIFIED_MODEL_FAILURE` produced mid-refinement halts the
   driver from inside `refine()`, not just `initial_pass()`.

Run: `python3 scripts/test_phase4_open_screening_driver_synthetic.py` → `ALL PASS`.

## Scope of this fix

Source-code change only, in a script that is explicitly allowed to be amended (it is not one of the
"frozen after first Open Gateway load" files listed in
`docs/test-plan/phase4-open-screening-protocol.md` §4 — that list is the harness/k6
scenario/collector/protocol values/Gateway source, not the orchestration driver itself). No load was
run to produce or validate this fix; it is a pure offline code + synthetic-test change, per this
Unit's "no new load" constraint.
