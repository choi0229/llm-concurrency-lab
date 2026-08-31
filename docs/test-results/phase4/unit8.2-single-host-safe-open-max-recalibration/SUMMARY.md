# Phase 4 Open Unit 8.2 — Single-host SAFE_OPEN_MAX Recalibration

**Result: `SAFE_OPEN_MAX_SINGLE_HOST_FINAL = 168`.**

Control-rig calibration only (M2 sentinel). No canonical Screening/Formal point, no model verdict
(CONTROL_SAFE / CONTROL_INVALID only). No sysctl / ulimit / topology change. One run per rate, no
retry. Policy frozen **before** the first run in
`docs/decisions/phase4-open-single-host-ephemeral-headroom.md`.

## Frozen safety criteria (recap)

- **§4a transport-cleanliness**: dropped 0 · EADDRNOTAVAIL 0 (k6 + Gateway `BindException`) · no
  dial/connect timeout · no unexplained zero-event · `open_attempt ≈ open_success` (≤0.1%) · Tomcat
  connections < 3200 · clock OK · no stall · postflight OK · host-safety clean.
- **§4b ephemeral headroom** (the frozen numeric threshold): `A∪B` unique-source-port **peak ≤ 80 %**
  of the 16 384-port host pool (**≤ 13 107**). Derivation: reciprocal of the project's unified
  **1.25×** headroom factor (`phase4-resource-safety-policy.md` §4; same factor as M3 WebClient and
  the Tomcat connector). 85 % is the emergency hard-stop, not the operating target.
- **§4c cliff**: the next-higher rate must show a clear, reproducible transport-pressure increase.
- **§4d**: Direct Mock R256 clean (Unit 8.1) ⇒ §4-of-safety-policy 1.25× rule independently caps any
  single-host Gateway claim at ⌊256/1.25⌋ = 204; §4b binds below that.

## Calibration data (M2, warmup 60 / measure 120, scenario 06, Tomcat maxConn 3200, final VU formula)

| R | class | LEG A uniq ports | LEG B uniq ports | **A∪B peak** | **% pool** | A∩B | LEG A TW | LEG B TW | dropped | open Δ | zero-ev | BindExc | Tomcat peak | VT-active peak (1st→2nd half) | CPU peak | clock |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 160† | safe (anchor) | ~6364 | ~6300 | **~12 700** | **~77.5 %** | ~1 | ~5108 | — | 0 | 0 | 0 | 0 | 1279/3200 | ~1280 flat | ~0.09 | clean |
| **164** | **CONTROL_SAFE** | 6541 | 6469 | **13 010** | **79.41 %** | 1 | 5235 | 5164 | 0 | 0 | 0 | 0 | 1341/3200 | 1339 (1282→1212) | 0.094 | clean |
| **168** | **CONTROL_SAFE** | 6572 | 6487 | **13 059** | **79.71 %** | 1 | 5234 | 5149 | 0 | 0 | 0 | 0 | 1356/3200 | 1354 (1311→1225) | 0.101 | clean |
| **176** | **CONTROL_INVALID** | 6992 | 6918 | **13 910** | **84.90 %** | 1 | 5616 | 5541 | 0 | 0 | 0 | 0 | 1442/3200 | 1440 (1375→1296) | 0.095 | clean |
| **192** | **CONTROL_INVALID** | 7639 | 7536 | **15 175** | **92.62 %** | 1 | 6095 | 5992 | 0 | 0 | 0 | 0 | 1596/3200 | 1594 (1510→1443) | 0.117 | clean |
| 256‡ | CONTROL_INVALID | 8282 | 8120 | **16 362** | **99.87 %** | 1 | 7824 | 7616 | 0 | 8215 | 6279 | 16 | 2343/3200 | 2341 (1723→1563) | 0.137 | clean |

† R160 = Unit 7.5/8 known-clean anchor; values from Unit 7.2 `m2-r160-finalvalue1` single-leg
telemetry (union estimated by doubling, A∩B≈1). **Not re-run** (governing instruction §10).
‡ R256 = Unit 8.1 M2 two-leg diagnostic (the original confound).

Calibration order: **192 → 176 → 164 → 168** (candidate 192 first per §5; invalid ⇒ refine down the
[160,192] bracket to 176; invalid ⇒ [160,176] → 164; safe ⇒ confirm boundary with 168). 4 of 4
budgeted points used. 224 / 240 not run — 192 already CONTROL_INVALID, higher rates strictly worse.

## Findings

1. **`A∪B` scales linearly at ≈ 79.3·R** (13010/164, 13059/168, 13910/176, 15175/192 all give
   77.7–79.3). `A∩B ≈ 1` at every rate — macOS never reuses a source port across the two
   destinations, so LEG A and LEG B ephemeral demands are disjoint and **always add**.
2. **Transport stays clean (0 EADDRNOTAVAIL, 0 dropped, 0 zero-event, 0 BindException) through
   R=192.** Actual connection failure first appears only at R=256. R=176/192 are CONTROL_INVALID
   purely on the **§4b headroom** criterion — they run inside the mandated 1.25× margin (< 20 % /
   < 8 % pool free), which the frozen policy disallows regardless of whether a socket has yet failed.
3. **Cliff (§4c) confirmed:** R164/R168 sit at 79.4–79.7 % (~3 300 ports free = exactly the 1.25×
   headroom); R176 jumps to 84.9 % (+851 ports), R192 to 92.6 %, R256 to 99.9 % + real EADDRNOTAVAIL.
   Monotone, reproducible.
4. **Gateway internals non-binding at every calibration rate:** Tomcat 1341→1596 / 3200, CPU peak
   0.09–0.12 (≈ 1 of 10 cores), VT-active **declining** within every run (1st-half mean > 2nd-half),
   `jvm_threads_live` flat, FD peak 2694–3204 / 1 048 576. The only thing that changes with R is the
   shared-host ephemeral-port occupancy — reconfirming the Unit 8.1 Case-B mechanism across the range.
5. **Clock clean every run** (`ttfc` p95 ≈ 1.04 s, completed-duration p95 ≈ 8.1 s, `iteration_duration`
   p95 ≈ 8.1 s — custom & built-in agree; no k6-saturation tail, unlike Unit 8.1's Direct Mock run).
6. **Sampler perturbation negligible** — two-leg sampler self-CPU 5.3–6.1 % of one core; every run
   started from `TIME_WAIT = 0`. No §12 STOP condition triggered (all INVALIDs are the expected,
   classifiable headroom-ceiling reproduction).

## Selection

Highest CONTROL_SAFE calibration rate = **R=168**; next-higher rate R=176 shows a clear reproducible
pressure increase (§4c). Per the frozen §5 rule → **`SAFE_OPEN_MAX_SINGLE_HOST_FINAL = 168`**.
It sits right at the 80 % headroom line (consumes the full 1.25× margin, no more). Conservative
alternative: the R=160 anchor (~77.5 %). The frozen rule yields 168.

## Artifacts

`m2-r{164,168,176,192}-cal1/` — each: `classification.json`, `k6-summary.json`,
`two-leg-socket-timeline.csv`, `raw-socket-observations.csv.gz` (~6–7 MB; gzip per ADR §6),
`lsof-ownership-samples.txt`, `gateway.log`, `prom-*.json`, `gateway-rss-samples.csv`,
`k6-rss-samples.csv`, `sampler-overhead.csv`, `host-baseline.txt`. No INVALID run produced a
`BindException`, so no uncompressed raw copy was retained (ADR §6). Total ≈ 59 MB for all four runs.

## Downstream (deferred to the user — no action taken in Unit 8.2)

- Existing canonical Open Screening points R ≤ 168 (M2/M3 R2–R160 in `unit8-open-screening/`) were
  collected under identical final semantics (frozen workload, `client.close()`, VU formula, Tomcat
  3200, collector), all `dropped=0`, all with union well under 80 % — **reusable without re-running**.
- Screening ceiling check: run **M2 R168** and **M3 R168** once each as fresh canonical points (168
  is not a base geometric rate). If both GREEN → `CONTROL_CENSORED`, `Screening MSAR ≥ 168`, no exact
  model MSAR, no M2 = M3 claim. If non-GREEN below 168 → MSAR bracket + normal refinement.
- M2 R256 → excluded from canonical model results, `CONTROL_INVALID` history. M3 R256 not run. M1
  unaffected (R10 model RED).
- Separate LoadGen host → reserved for a future `Phase 4.2 Extended Open Boundary` (different epoch).
