# Phase 4 Open Formal — Protocol Freeze

Status: **FROZEN** (Phase 4 Open Unit 8.3 → Unit 9 boundary). Date: 2026-08-30.
Screening authority: `docs/test-results/phase4/unit8-open-screening/UNIT8.3-COMPLETION.md`
(Open Screening = PASS / COMPLETE / FROZEN). Control authority:
`docs/decisions/phase4-open-single-host-ephemeral-headroom.md`
(`SAFE_OPEN_MAX_SINGLE_HOST_FINAL = 168 req/s`).

This document freezes the cells, predicates, confirmation rule, run order, and Canary. **No Formal
matrix run is authorized by this document** — the 12-run matrix requires separate explicit approval
after the Canary is reported.

## 1. Measurement window (already frozen — not changed here)

`docs/test-plan/phase4-open-screening-protocol.md` §2.1:

| phase | warmup | measurement |
|---|---|---|
| Screening | 60 s | 120 s |
| **Formal** | **120 s** | **300 s** |

Single continuous k6 process per run (warmup immediately followed by measurement, no restart/drain
between). Recorded in each run's `environment.json` as `warmup_sec` / `measurement_sec`; the
collector (`collect_phase4_open_result.py`) is window-agnostic and reads them from there.

## 2. Frozen control contract (inherited, unchanged)

`client.close()` FINAL lifecycle · `OPEN_TOMCAT_MAX_CONNECTIONS = 3200` ·
`WEBCLIENT_MAX_CONNECTIONS = 3200` / `pendingAcquireMaxCount = 3200` · final VU formula
(`PRE_ALLOCATED_VUS = RATE·60`, `MAX_VUS = ceil(PRE·1.25)`) · workload 1000/200/35/64 ·
`CHAT_TOTAL_TIMEOUT_MS = 60000` · Mock unlimited · `dropped_iterations == 0` mandatory ·
clock-integrity cross-check · stall checker · PID/RSS/FD samplers · postflight ·
client/Gateway/Mock accounting. No production source change. No sysctl. No separate LoadGen host.
`R > 168` not run.

## 3. Formal cells (FROZEN — exactly 4)

| cell | model | rate | role | boundary predicate (PASS iff) | expected signature |
|---|---|---|---|---|---|
| **F1** | M1 | R6 | MSAR lower endpoint | `sustainable == true` (i.e. `model_outcome.color == "GREEN"`) | GREEN |
| **F2** | M1 | R7 | MSAR upper endpoint | `sustainable == false` (i.e. `model_outcome.color != "GREEN"`) | AMBER (`MODEL_SATURATION` shape) |
| **F3** | M2 | R168 | control-censored lower-bound confirmation | `GREEN == true` (`model_outcome.color == "GREEN"`) | GREEN |
| **F4** | M3 | R168 | control-censored lower-bound confirmation | `GREEN == true` (`model_outcome.color == "GREEN"`) | GREEN |

- **F2 predicate is a boundary predicate, not a colour test.** F2 PASSes on any VALID non-GREEN
  outcome (AMBER *or* RED). Whether the AMBER signature reproduces is recorded **separately**
  (`signature_match`), so boundary confirmation and exact-colour reproduction are not conflated.
- `sustainable` for a GREEN-expected cell (F1/F3/F4) == `color == "GREEN"`.

## 4. Confirmation semantics (FROZEN — reuses the existing Closed Formal vocabulary; no new labels)

PASS/FAIL is **the cell predicate**, not run success. Every run is first gated VALID / INVALID;
INVALID runs never enter a cell's replicate count and are never deleted (raw preserved).

Initial **n = 3** valid reps per cell:

| outcome | result |
|---|---|
| 3 PASS / 0 FAIL | `CONFIRMED SUSTAINABLE` (F1/F3/F4) · `CONFIRMED` (F2) |
| 2 PASS / 1 FAIL | `AMBIGUOUS` → exactly **2** additional valid reps (n = 5) |
| ≤ 1 PASS / ≥ 2 FAIL | `CONFIRMED UNSTABLE` (F1/F3/F4) · `NOT CONFIRMED` (F2) |

Final **n = 5** (only from an AMBIGUOUS 2/1 seed):

| outcome | result |
|---|---|
| 4 PASS / 1 FAIL | `CONFIRMED SUSTAINABLE` / `CONFIRMED` |
| 3 PASS / 2 FAIL | `INCONCLUSIVE RANGE` |
| 2 PASS / 3 FAIL | `CONFIRMED UNSTABLE` / `NOT CONFIRMED` |

No cell is repeated once it reaches a terminal state. No "nicer result" pursuit.

## 5. Formal-confirmed wording (FROZEN — anti-overclaim)

- F1 **and** F2 both CONFIRMED → **"Formal-confirmed Open MSAR bracket = [6, 7]"**. Never
  "MSAR = 6" or "MSAR = 7".
- F3 CONFIRMED → **"M2 Formal: Open MSAR ≥ 168, CONTROL_CENSORED"**.
- F4 CONFIRMED → **"M3 Formal: Open MSAR ≥ 168, CONTROL_CENSORED"**.
- Forbidden regardless of results: `MSAR = 168`, `M2 = M3`, "VT ceiling = WebFlux ceiling",
  "168 is the architecture limit". 168 is the single-host control-valid maximum.
- M1 R10 `MODEL_REJECTION` stays a **Screening secondary reliability observation**; no R10 Formal
  cell is added (no run-count padding).

## 6. M2 R256 — excluded

`CONTROL_INVALID HISTORY` (Unit 8.1 Case B, shared-host ephemeral-port confound). Not a model RED,
not an MSAR upper endpoint, not a Formal cell. Raw retained.

## 7. Stability Canary (FROZEN — run once, BEFORE the matrix)

- **Cell:** M2 R168 (the Formal cell closest to the M2-sentinel single-host ephemeral-port
  operational ceiling — 79.71 % of pool at R168 per Unit 8.2).
- **Purpose:** stability gate for the longer Formal lifecycle (120 s + 300 s) — confirms transport /
  control headroom and the measurement harness hold over the longer window. **Not** a performance
  re-check, **not** a Screening point, **not** F3 rep1.
- **Isolation:** output under `docs/test-results/phase4/unit9-open-formal/m2-r168-canary/`;
  `canary-result.json` at `unit9-open-formal/`. Never named like an F-cell replicate.
- **Telemetry:** lightweight endpoint-aware only (5 s cadence connection-count sampler + pre/post
  TIME_WAIT snapshot). The heavy Unit 8.1/8.2 per-socket raw sampler is **not** attached — Unit 8.2
  calibration is the R168 control-safety authority; the Canary does not redefine the 80 % rule as a
  model validity threshold.
- **Canary PASS gate (all must hold):** `valid == true` · `dropped_iterations == 0` ·
  actual arrival within ±5 % of 168 · cohort `invariant_ok == true` · `color == "GREEN"` ·
  `stall_detected == false` · `clock_integrity_ok == true` · `postflight_clean == true` ·
  `tomcat_connections_non_binding == true` · 0 `BindException` / 0 `EADDRNOTAVAIL` ·
  0 dial/connect timeout · no unexplained client/Gateway/Mock divergence · TIME_WAIT postflight not
  abnormally elevated (host returns toward baseline).
- **Canary FAIL / INVALID / non-GREEN → STOP.** No auto-retry. No matrix start. Preserve artifacts,
  report.
- **Canary PASS does NOT auto-start the matrix** — report to the user, await separate approval.

### 7.1 Canary outcome (recorded)

- Attempt 1 (`m2-r168-canary/`, `canary-result.json`): **CANARY GATE INSTRUMENTATION INVALID** —
  the RUN was VALID / GREEN / transport-stable over the full 300 s window; only the
  `time_wait_returns_toward_baseline` gate check was mis-implemented (sampled TIME_WAIT at
  drain-end, before the ~2·MSL decay, against a ≤ 3000 threshold; `at_drain_end` ≈ 9781 is the
  expected R=168 steady-state residue). Not a model or control failure. Preserved read-only.
  Evidence: `docs/test-results/phase4/unit9-open-formal/canary-gate-defect-note.md`.
- Gate correction (`scripts/run_phase4_open_formal.py`): **sample time only** — TIME_WAIT now read
  **75 s post-drain**; **threshold unchanged (≤ 3000)**; `at_drain_end` kept as a diagnostic field;
  missing/malformed post-decay sample → **fail-closed**. Synthetic regression
  `scripts/test_phase4_open_formal_canary_synthetic.py` — ALL PASS. No production / measurement /
  predicate / confirmation / order change.
- Attempt 2 (`m2-r168-canary-gatefix-replacement/`,
  `canary-result-canary-gatefix-replacement.json`): **PASS** (all 13 checks). VALID / GREEN,
  started = completed = 50401, dropped 0, TTFC p95 1.028 s, dur p95 8.066 s, Tomcat 1366/3200
  non-binding, 0 BindException / 0 EADDRNOTAVAIL, transport flat over 300 s,
  `time_wait_at_drain_end` = 9793 → `time_wait_75s_post_drain` = **0**. → **Open Formal Stability
  Canary = PASS (FROZEN).** The replacement is a Canary only; **not** F3 M2 R168 rep1.
- **Canonical Formal replicate count = 0** (no F-cell has been run).

## 8. Frozen 12-run matrix order (codified — NOT executed by this freeze)

4 cells × 3 valid fresh repeats = **12 initial Formal runs**. Balanced literal order (each cell once
per block; distributes model / load / time-order bias):

```
Block 1:  F1(M1 R6) · F3(M2 R168) · F2(M1 R7) · F4(M3 R168)
Block 2:  F4(M3 R168) · F2(M1 R7) · F3(M2 R168) · F1(M1 R6)
Block 3:  F2(M1 R7) · F1(M1 R6) · F4(M3 R168) · F3(M2 R168)
```

Replicate dirs: `f1-m1-r6-rep{1,2,3}`, `f2-m1-r7-rep{1,2,3}`, `f3-m2-r168-rep{1,2,3}`,
`f4-m3-r168-rep{1,2,3}` under `unit9-open-formal/`. Screening / calibration / Canary artifacts are
**never** reused as Formal replicates.

AMBIGUOUS-resolution extras (max 2 per cell, only after all 12 initial reps complete, in cell-ID
order F1→F2→F3→F4, each cell's 2 extras back-to-back): `…-rep4`, `…-rep5`.

## 9. Aggregation contract (for when the matrix runs)

Categorical (never averaged): per-cell valid count, predicate PASS/FAIL per rep, confirmation state,
colour distribution, failure-signature distribution.
Numeric (median / min / max / CV / n): arrival rate, completion throughput, completion ratio,
backlog trend, TTFC p95, stream-duration p95, platform threads, CPU, RSS, FD; plus
M1 {executor active, queue depth, queue wait, rejection, timeout}, M2 {virtual_tasks_active,
tomcat_connections}, M3 {reactor active, pending, connection_limit_evaluable/binding}.

## 10. Final interpretation (for when the matrix completes)

- F1 & F2 both CONFIRMED → **M1 Formal-confirmed Open MSAR bracket [6, 7]**.
- F3 CONFIRMED → **M2 Formal: Open MSAR ≥ 168, CONTROL_CENSORED**.
- F4 CONFIRMED → **M3 Formal: Open MSAR ≥ 168, CONTROL_CENSORED**.
- **M2 vs M3 exact Open ceiling ranking: INCONCLUSIVE** (both control-censored at the single-host
  ceiling).
- Open (arrival sustainability) and Closed (concurrency) boundaries stay separate experiments until
  a Final Report; not combined in this Unit.
