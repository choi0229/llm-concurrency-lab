# Phase 4.1 (Unit 6.5) — Extended Direct Mock Closed Control Calibration

Per `docs/decisions/phase4-scalability-definition.md` §16-3 Option B (explicitly authorized by
the user for this exact scenario) and §16-5 (the required "new control range → headroom policy
재계산 → M3 maxConnections 재-freeze" sequence). Same methodology as Unit 3
(`scripts/run-phase4-direct-mock-closed-control-extended.sh`, k6/xk6-sse -> Mock LLM directly, no
Gateway) — Unit 3's own script/output are untouched.

## Combined evidence (Unit 3 original + this unit's new levels)

| N | completed | failed | dropped | TTFC p95 | duration p95 | classification | source |
|---|---|---|---|---|---|---|---|
| 100 | 100 | 0 | 0 | 1.033s | 7.911s | CLEAN | Unit 3 (reused, unchanged) |
| 200 | 200 | 0 | 0 | 1.048s | 7.943s | CLEAN | Unit 3 (reused, unchanged) |
| 400 | 400 | 0 | 0 | 1.096s | 7.990s | CLEAN | Unit 3 (reused, unchanged) |
| 800 | 800 | 0 | 0 | 1.182s | 8.076s | CLEAN_ROBUST | Unit 3 (reused, unchanged) |
| **1000** | 1000 | 0 | 0 | 1.135s | 8.184s | CLEAN | **new (this unit)** |
| **1200** | 1200 | 0 | 0 | 1.215s | 8.183s | CLEAN | **new (this unit)** |
| **1400 (run1)** | 1400 | 0 | 0 | 1.218s | 8.223s | CLEAN | **new (this unit)** |
| **1400 (run2, repeat)** | 1400 | 0 | 0 | 1.246s | 8.434s | CLEAN | **new (this unit)** |
| 1600 | 1600 | 0 | 0 | 2.091s / 5.03s (2 runs) | 9.155s / 12.12s (2 runs) | CLEAN_MARGINAL | Unit 3 (reused, unchanged — NOT re-tested here) |
| 3200 | 2415 | 785 | 0 | 6.926s | 13.926s | FAIL (LOADGEN_LIMIT) | Unit 3 (reused, unchanged — NOT re-tested here) |

## N=1400 robustness verification

Two independent runs at N=1400: TTFC p95 1.218s vs 1.246s (+2.3%), duration p95 8.223s vs 8.434s
(+2.6%) — both well within normal run-to-run noise, nothing resembling Unit 3's own observed 1600
instability (2.4x variance between two runs there). **N=1400 is classified CLEAN_ROBUST**, using
Unit 3's own precedent for what "robust" requires (repeat-verified low variance, not a single
clean pass).

N=1600 was NOT re-tested — Unit 3's existing two runs already characterize it as CLEAN_MARGINAL,
and re-running it now would not change that classification's basis (the instability was between
two independent Unit-3-era runs; a third run here wouldn't resolve or need to resolve that
history). Per policy, a marginal level is never used as the headroom-computation basis regardless
of how many additional runs might show it clean.

## SAFE_CLOSED_MAX_EXTENDED

```
robust_clean_ceiling_N_extended = 1400
SAFE_CLOSED_MAX_EXTENDED = 1400 / 1.25 = 1120
```

Deliberately not based on N=1600 (marginal) or anything higher. **1120 is frozen as
SAFE_CLOSED_MAX_EXTENDED.** No level above 1400 was tested (1600 already known marginal, 3200
already known FAIL) — Extended Closed screening for M2/M3 will use candidate levels 800, 1000,
1120 only (all <= SAFE_CLOSED_MAX_EXTENDED), per protocol.

## M3 ConnectionProvider re-freeze (per §16-5)

```
EXTENDED_M3_MAX_CONNECTIONS = ceil(SAFE_CLOSED_MAX_EXTENDED * 1.25) = ceil(1120 * 1.25) = 1400
WEBCLIENT_PENDING_ACQUIRE_MAX_COUNT = 1400 (same headroom policy: sized equal to maxConnections)
```

`WEBCLIENT_PENDING_ACQUIRE_TIMEOUT_MS` / `WEBCLIENT_CONNECT_TIMEOUT_MS` unchanged (10000ms/3000ms
— unrelated to headroom policy, per the original §7-1-1 freeze). This is a pre-screening headroom
amendment (removing a hidden configured ceiling for the newly-extended target range), not a
result-driven tuning — applied before any Extended Closed Gateway load runs, per instruction.

## Root-cause restraint

N=3200's failure (Unit 3) remains classified `LOADGEN_LIMIT` (primary candidate, with possible
`DOWNSTREAM_LIMIT` contribution not fully isolated) — not re-litigated or re-characterized by this
unit's narrower 1000-1400 exploration, which never approached that range.

## Frozen source/harness diff

`load-test-k6/scenarios/01-concurrent-connections.js` and `mock-llm-fastapi/app/main.py`: unchanged
since Unit 3 (confirmed by mtime — no edits). The extended calibration script
(`scripts/run-phase4-direct-mock-closed-control-extended.sh`) is a new file, byte-identical in
methodology to Unit 3's `run-phase4-direct-mock-closed-control.sh` except `LEVELS`/`OUT_ROOT`; the
original Unit 3 script is untouched.
