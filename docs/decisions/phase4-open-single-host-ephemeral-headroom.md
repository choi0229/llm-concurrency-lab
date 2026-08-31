# ADR: Phase 4 Open single-host ephemeral-port headroom & SAFE_OPEN_MAX recalibration

Status: **Accepted / frozen — this document is frozen BEFORE the first Unit 8.2 calibration run**
(governing instruction Unit 8.2 §9: any numeric threshold must be frozen in the doc before the first
run, contrasted with existing control-safety policy).
Date: 2026-08-30 · Scope: Phase 4 Open, single-host native-macOS control rig, epoch
`open-final-client-lifecycle-v1`.

## 1. Why `SAFE_OPEN_MAX = 256` is retired (not deleted)

`SAFE_OPEN_MAX = 256` was frozen in `docs/test-plan/phase4-open-screening-protocol.md` §2.2 and
propagated into every Open harness (`OPEN_TOMCAT_MAX_CONNECTIONS`, `WEBCLIENT_MAX_CONNECTIONS`, the
VU formula). Phase 4 Open **Unit 8.1** (`docs/test-results/phase4/unit8.1-m2-r256-two-leg-forensics/`)
established, with two-leg endpoint-aware socket telemetry, that at R=256 on this single-host rig the
k6→Gateway leg (~8282 unique ephemeral ports peak) and the Gateway→Mock leg (~8120) have **disjoint**
port demands (A∩B ≈ 1) that sum to **A∪B ≈ 16362 of the host's 16384-entry ephemeral-port pool**,
producing `EADDRNOTAVAIL` on both legs (16 Gateway `BindException`s + 6279 silent k6 connect
failures) while every Gateway-internal resource stayed non-binding (VT backlog declining, CPU ~1.4/10
cores, Tomcat 2343/3200).

**Correction recorded:** the `SAFE_OPEN_MAX = 256` calibration (Unit 3 open control, Unit 7.3/7.5
re-freezes) validated the **Direct Mock single-leg** path and the **Tomcat connector** ceiling, but
**never modelled the two-leg same-host ephemeral-port constraint of the final two-leg Open workload**
— k6 and the Gateway share one host and one ephemeral-port pool, and their combined outbound churn
was not part of any control check. `SAFE_OPEN_MAX = 256` is **kept as historical calibration**, not
deleted, and is superseded for all single-host Open work by `SAFE_OPEN_MAX_SINGLE_HOST_FINAL`.

## 2. New name

`SAFE_OPEN_MAX_SINGLE_HOST_FINAL` — the highest Open arrival rate that is **CONTROL_SAFE** on this
single-host rig per §4 below. Value set by Unit 8.2 calibration (`docs/test-results/phase4/
unit8.2-single-host-safe-open-max-recalibration/`).

**`SAFE_OPEN_MAX_SINGLE_HOST_FINAL = 168`** (Unit 8.2, M2 sentinel, one run per rate, no retry):

| R | class | A∪B peak | % of 16 384 pool | note |
|---|---|---|---|---|
| 160 | control-safe (anchor) | ~12 700 (est.) | ~77.5 % | Unit 7.5/8 known-clean; single-leg telemetry only, not re-run (§10) |
| **164** | **CONTROL_SAFE** | 13 010 | 79.41 % | 0 EADDRNOTAVAIL, all §4a clean |
| **168** | **CONTROL_SAFE** | 13 059 | 79.71 % | 0 EADDRNOTAVAIL, all §4a clean — **selected** |
| 176 | CONTROL_INVALID | 13 910 | 84.90 % | §4b headroom breach (no failure yet — clear pressure rise, §4c cliff) |
| 192 | CONTROL_INVALID | 15 175 | 92.62 % | §4b breach |
| 256 | CONTROL_INVALID | 16 362 | 99.87 % | actual EADDRNOTAVAIL (Unit 8.1) — the original confound |

`A∪B` scales linearly at ≈ 79.3 · R; `A∩B ≈ 1` at every rate (the two legs' ephemeral ports are
always disjoint, demands always add). Actual `EADDRNOTAVAIL` first appears only at R=256; R=176/192
are INVALID on the **headroom** criterion (operating inside the mandated 1.25× margin), not on
observed failure.

## 3. Environment constraints for Unit 8.2 (frozen)

- **No sysctl / `ulimit` / socket-tuning changes.** `net.inet.ip.portrange` stays 49152–65535
  (16384 ports), `net.inet.tcp.msl` stays 15000 ms (TIME_WAIT = 2·MSL = 30 s). Measure the current
  host semantics.
- **No separate LoadGen host.** localhost single-host topology retained. A separate LoadGen host is
  reserved for a future `Phase 4.2 Extended Open Boundary` under a *different* environment epoch; it
  does not replace or rebaseline the current Open results.
- **M2 is the calibration sentinel** — in the current Open setup M2 is the known worst-case
  two-leg transport path (k6→Gateway + Gateway→Mock blocking `HttpURLConnection`, both legs'
  ephemeral-port pressure simultaneously observable; it reproduced the R256 confound). CONTROL_SAFE
  on M2 → conservative control-ceiling candidate for M3 on the same host. This does **not** assert
  "M2 always uses the most ports of any architecture."

## 4. CONTROL_SAFE criteria — FROZEN before first calibration run

A calibration rate R is **CONTROL_SAFE** iff **all** of the following hold; otherwise
**CONTROL_INVALID**. No GREEN/AMBER/RED — this is a control-rig classification, not a model verdict.

### 4a. Transport-cleanliness (from `phase4-resource-safety-policy.md` §3, §5, §6)
- `dropped_iterations = 0`
- `EADDRNOTAVAIL` count = 0 (k6 side AND Gateway `BindException` side)
- dial / connect timeout = 0
- unexplained zero-event terminal = 0 (a small, *fully accounted* zero-event count from a
  non-transport cause would be examined on its merits; any connect-stage zero-event → INVALID)
- `client_sse_open_attempt ≈ client_sse_open_success` (deficit ≤ 0.1%)
- Tomcat `tomcat_connections_current` non-binding (< configured `maxConnections` = 3200)
- clock integrity OK · no environment stall / log gap · postflight clean · host-safety clean
  (no `EMFILE`, no memory-pressure critical, no crash)

### 4b. Ephemeral-port headroom — the new numeric threshold, frozen here
- **`A ∪ B` unique-source-port peak ≤ 80 % of the host ephemeral pool = ≤ 13 107 of 16 384.**

  **Derivation (not arbitrary):** `phase4-resource-safety-policy.md` §4 freezes a single unified
  Phase 4 headroom factor of **1.25×** ("Phase 4 전체에서 'headroom'이라는 개념을 하나의 숫자로
  통일"), already applied identically to M3's `WebClient.ConnectionProvider` and to the Tomcat
  connector budget (`phase4-open-tomcat-connector-headroom.md`: `ceil(load · holding_time · 1.25)`).
  Operating a resource at 1.25× headroom means steady-state occupancy ≤ 1 / 1.25 = **0.80** of the
  ceiling. Applying that same factor to the ephemeral-port pool gives the 80 % line. The 85 % figure
  in `phase4-resource-safety-policy.md` §3 is the **emergency hard-stop**, not the operational
  target; 80 % is the target, 85 % is the wall.

### 4c. Cliff separation (from Unit 8.2 §9)
- The **next-higher** calibration rate must show a **clear, reproducible increase in transport
  pressure** (higher `A ∪ B` peak, and/or onset of `EADDRNOTAVAIL`, and/or ESTABLISHED-collapse
  episodes). A rate that is CONTROL_SAFE only because it happened to land a few ports below the pool
  with no margin is **not** SAFE — 4b already enforces this, and 4c is the qualitative confirmation.

### 4d. Direct Mock control headroom (independent cap, from §4 of the safety policy)
- Unit 8.1 Run 1 showed **Direct Mock R=256 is transport-clean** (single-leg, 10324/16384 ports, 0
  `EADDRNOTAVAIL`). The §4 rule "Open: direct Mock control ≥ 1.25× the Gateway's claimed rate"
  therefore independently caps any single-host Gateway Open claim at **⌊256 / 1.25⌋ = 204**. If the
  4b ephemeral ceiling lands below 204 (expected), 4b binds; 204 is the ceiling only if 4b somehow
  lands higher.

## 5. `SAFE_OPEN_MAX_SINGLE_HOST_FINAL` selection rule (frozen)

The value is the **highest calibration rate that is CONTROL_SAFE by §4**, provided §4c holds at the
next-higher rate. No post-hoc adjustment of the 80 % line. If the highest CONTROL_SAFE rate is the
lower anchor R=160 itself, `SAFE_OPEN_MAX_SINGLE_HOST_FINAL = 160`.

**Applied:** R=168 is CONTROL_SAFE (79.71 %); R=176 is CONTROL_INVALID with a clear, reproducible
pressure increase (84.90 %, +851 ports); §4c satisfied → **`SAFE_OPEN_MAX_SINGLE_HOST_FINAL = 168`**.
Note this sits right at the 80 % headroom line — it consumes the full mandated 1.25× margin and no
more. A more conservative operator may prefer the R=160 anchor (~77.5 %); the frozen §5 rule yields
168 and that is what is adopted. `SAFE_OPEN_MAX_SINGLE_HOST_FINAL` is **not** a base geometric
Screening rate, so the Screening ceiling check runs it as an explicit fresh point (§ Unit 8.2 report
items 20–21).

## 6. Raw-artifact preservation for calibration (Unit 8.2 §7 decision)

The Unit 8.1 two-leg sampler's correctness is already validated. For Unit 8.2:
- the sampler **still writes the full normalized raw socket-row CSV every run** (columns
  `epoch_ms,leg,local_addr,local_port,foreign_addr,foreign_port,state`) — auditability is not
  reduced;
- the runner **gzip-compresses that CSV at run end** (~140 MB → ~15–20 MB; the rows are highly
  repetitive). `raw-socket-observations.csv.gz` is the retained form. Any **CONTROL_INVALID** run
  additionally keeps an uncompressed copy.
- the per-second `two-leg-socket-timeline.csv` is always retained uncompressed.

This preserves full recomputability (Unit 7.4 lesson) at ~1/8 the storage.

## 7. Not decided here (post-Unit-8.2)

Existing-canonical-point reuse, the next canonical Screening point per model, Open Screening resume,
Open Formal — all deferred to the Unit 8.2 report and the user's decision. No canonical resume run,
no Formal, in Unit 8.2.
