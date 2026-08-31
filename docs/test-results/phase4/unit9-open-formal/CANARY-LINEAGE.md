# Open Formal Stability Canary — lineage

| attempt | dir | result file | classification | disposition |
|---|---|---|---|---|
| 1 (original) | `m2-r168-canary/` | `canary-result.json` | **CANARY GATE INSTRUMENTATION INVALID** — RUN was VALID/GREEN/transport-stable; only the `time_wait_returns_toward_baseline` gate check was mis-implemented (sampled TIME_WAIT at drain-end, before the ~2·MSL decay). See `canary-gate-defect-note.md`. | preserved read-only; NOT a model/control failure; NOT a Formal replicate |
| 2 (replacement) | `m2-r168-canary-gatefix-replacement/` | `canary-result-canary-gatefix-replacement.json` | corrected-gate validation | Canary only; NOT F3 M2 R168 rep1 |

Gate correction (scripts/run_phase4_open_formal.py): **sample time only** — TIME_WAIT is now read
**75 s post-drain** (>> the host's ~2·MSL ≈ 30 s decay) instead of at drain-end. Pass threshold
**unchanged (≤ 3000)**. `time_wait_at_drain_end` retained as a diagnostic field. Missing / malformed
post-decay sample → **fail-closed**. No production / measurement / predicate / confirmation / order
change. Synthetic regression: `scripts/test_phase4_open_formal_canary_synthetic.py` (ALL PASS).

Canonical Formal replicate count remains **0** (no F-cell has been run).
