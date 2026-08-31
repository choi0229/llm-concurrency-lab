# Phase 4 Open Unit 8.3 — Canonical Screening Completion + MSAR Refinement

**Open Screening = COMPLETE.** Epoch `open-final-client-lifecycle-v1`. Control ceiling
`SAFE_OPEN_MAX_SINGLE_HOST_FINAL = 168` (Unit 8.2). No Open Formal run in this Unit.

Second preflight PASS (AC connected & powering, arm64 native / no Rosetta, Docker engine down,
no stale Gateway/Mock/Prometheus/k6/sampler, all target ports free, TIME_WAIT baseline clean,
Temurin 21.0.11+10, k6 v1.8.0 + xk6-sse v0.1.11, `caffeinate` active, load avg ~1.9).

## New canonical runs (4; all VALID first attempt, 0 retries, 0 INVALID)

| dir | model | R | valid | color | classification | started=completed | dropped | TTFC p95 | dur p95 | notes |
|---|---|---|---|---|---|---|---|---|---|---|
| `m2-r168-screen1` | M2 | 168 | ✓ | **GREEN** | GREEN | 20160 | 0 | 1.023 s | 8.059 s | Tomcat conn peak 1370/3200 non-binding; VT-active declining; 0 BindException |
| `m3-r168-screen1` | M3 | 168 | ✓ | **GREEN** | GREEN | 20161 | 0 | 1.024 s | 8.114 s | reactor active peak 1373, **pending peak 0** → connection-limit non-binding; gateway_active declining |
| `m1-r7-refine1` | M1 | 7 | ✓ | **AMBER** | AMBER | 839 | 0 | 17.832 s | 24.681 s | reliability PASS (0 rejection); SLO fail + backlog growth; executor 50/50 busy, queue depth 110 |
| `m1-r6-refine2` | M1 | 6 | ✓ | **GREEN** | GREEN | 721 | 0 | 1.006 s | 7.848 s | executor active peak 47/50, queue depth 0, no backlog growth |

All 4: `clock_integrity_ok=true`, `stall_detected=false`, `postflight_clean`, every validity check
pass, `server_outcomes` all zero (upstream_error / timeout / internal_error / client_disconnect),
Mock population consistent (mock_completed = whole-process server completed), **0 EADDRNOTAVAIL /
0 BindException**. No §10 STOP condition triggered.

## Per-model outcome

### M1 — MSAR bracket found
Starting bracket [4 GREEN, 10 RED `MODEL_REJECTION`].
Refinement (frozen rule: `mid = round((low+high)/2)`, GREEN→low=mid else high=mid, stop when
`(high-low)/low ≤ 0.20` or 4 points):
1. R7 → AMBER → bracket [4, 7]
2. R6 → GREEN → bracket [6, 7]; relative width (7−6)/6 = **16.7 % ≤ 20 % → STOP** (2 of 4 points used).

**M1 final Open Screening MSAR bracket = [6, 7]** (highest GREEN 6, first non-GREEN 7).
**Reliability boundary observation:** R6 GREEN → R7 AMBER (reliability still PASS: 839/839 complete,
0 rejection — SLO blown, TTFC/dur p95 17.8 s / 24.7 s, queue growing = `MODEL_SATURATION` shape) →
R10 RED (`MODEL_REJECTION`, `reliability_pass=false`, completion_ratio 0.291, AbortPolicy). The
reliability boundary (last rate at which every request still completes) lies in **(7, 10]** — not
further refined; the MSAR bracket is the primary Screening target and is complete.

### M2 — control-censored
R2–R160 GREEN (existing) + **R168 GREEN** (new) → **CONTROL_CENSORED: Screening MSAR ≥ 168**
(= `SAFE_OPEN_MAX_SINGLE_HOST_FINAL`). No exact model MSAR found. No refinement (R168 GREEN, §7).
R > 168 not run. `MSAR = 168` is **not** claimed.

### M3 — control-censored
R2–R160 GREEN (existing) + **R168 GREEN** (new) → **CONTROL_CENSORED: Screening MSAR ≥ 168**.
Connection-limit `evaluable=true`, `binding=false` (reactor pending peak 0). No refinement. R > 168
not run.

## M2 R256 — CONTROL_INVALID history (not a model result)

`m2-r256-screen1` raw is retained. Its recorded `RED / UNCLASSIFIED_MODEL_FAILURE` is superseded by
the Unit 8.1 forensic verdict: **Case B — shared-host ephemeral-port confound** (k6 + Gateway share
one 16 384-entry pool; A∪B ≈ 16 362 with A∩B ≈ 1). It is **excluded** from highest-GREEN /
first-non-GREEN, the MSAR bracket, the reliability boundary, and Formal cell selection. Not a
Virtual-Thread finding, not an architecture RED.

## Final Open Screening table

| Model | highest GREEN | first non-GREEN | refined MSAR bracket | reliability boundary | control-censored |
|---|---|---|---|---|---|
| **M1** | R6 | R7 (AMBER) | **[6, 7]** (rel. width 16.7 %) | in (7, 10] — R7 reliability PASS, R10 `MODEL_REJECTION` | no |
| **M2** | R168 | — (none ≤ 168) | — | not reached ≤ 168 | **yes** — MSAR ≥ 168 |
| **M3** | R168 | — (none ≤ 168) | — | not reached ≤ 168 | **yes** — MSAR ≥ 168 |

M2 vs M3 exact ranking: **impossible** — both are control-censored at the single-host ceiling; the
data support only `M2 MSAR ≥ 168` and `M3 MSAR ≥ 168`. No `M2 = M3`, no "VT ceiling = WebFlux
ceiling", no "168 is the architecture limit". 168 is the single-host control-valid maximum, not an
architecture ceiling.

## Open Screening FINAL = PASS

All three models resolved (M1 refined bracket; M2/M3 control-censored), no unresolved INVALID, no
control confound recurrence, host/postflight clean.

## Recommended Open Formal cells (recommendation only — NOT run in this Unit)

| cell | rate | basis |
|---|---|---|
| M1 lower | R6 | highest GREEN of the [6,7] bracket |
| M1 upper | R7 | first non-GREEN (AMBER) of the bracket |
| M2 | R168 | control-censored single cell |
| M3 | R168 | control-censored single cell |

**4 cells × 3 valid fresh Formal repeats = 12 initial Formal runs** (not fixed until Formal is
authorized; Screening artifacts must not be reused as Formal replicates). Formal confirmation
semantics unchanged (3/0 CONFIRMED · 2/1 → 2 extra · 4/1 CONFIRMED · 3/2 INCONCLUSIVE RANGE · ≤2/3
CONFIRMED UNSTABLE). **Open Formal readiness = PASS**, but Formal is not started here.
