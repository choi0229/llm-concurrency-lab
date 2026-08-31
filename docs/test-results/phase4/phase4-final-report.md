# Phase 4 — Concurrency-Model Scalability Boundaries: Final Report

**Status: COMPLETE / FROZEN.** All numbers in this report are read read-only from canonical
aggregates and raw result.json files; none are written from memory. Canonical sources:

- Closed Formal: `docs/test-results/phase4/unit6-closed-formal/formal-aggregate.json`,
  `formal-summary-final.json`
- Extended Closed Formal (Phase 4.1): `docs/test-results/phase4/unit6.7-extended-closed-formal/`
  (`extended-formal-summary.json`, `SUMMARY.md`)
- Open Screening: `docs/test-results/phase4/unit8-open-screening/UNIT8.3-COMPLETION.md`,
  `screening-summary-unit8.3.json`
- Open control calibration: `docs/test-results/phase4/unit8.2-single-host-safe-open-max-recalibration/SUMMARY.md`,
  `docs/decisions/phase4-open-single-host-ephemeral-headroom.md`
- Open Formal: `docs/test-results/phase4/unit9-open-formal/formal-aggregate.json`,
  `formal-summary.json`, `UNIT9-COMPLETION.md`
- Transport forensics: `docs/test-results/phase4/unit8.1-m2-r256-two-leg-forensics/`

---

## 1. Executive Summary

Three server concurrency architectures were compared on one fixed runtime, host, and streaming
workload, along two orthogonal scalability axes:

- **Closed** (how many concurrent SSE streams can be *held* at once, with SLO + reliability):
  - **M1 (Platform-thread + bounded queue)**: Formal-confirmed **MSC bracket [50, 56]** and
    **MRC bracket [350, 400]** — a real, found boundary.
  - **M2 (Virtual-thread)** and **M3 (WebFlux)**: **MSC ≥ 1120, MRC ≥ 1120, CONTROL-CENSORED** —
    no architecture boundary was found within the control-valid range.
- **Open** (what constant arrival rate can be *sustained* without backlog growth, with SLO +
  reliability):
  - **M1**: Formal-confirmed **MSAR bracket [6, 7] req/s**.
  - **M2** and **M3**: **MSAR ≥ 168 req/s, CONTROL-CENSORED** (168 = the single-host benchmark
    rig's transport-safe ceiling, not an architecture limit).
- **M2 vs M3 exact ceiling ranking: INCONCLUSIVE** on both axes — both are censored by the test
  rig, not separated by the data.

The headline engineering finding is not "who is fastest" (at equal admitted load the three are
latency/throughput-indistinguishable) but **where and how each first stops being sustainable**:
M1's boundary is queue-wait latency (SLO breaks long before the queue overflows to rejection); M2
and M3 have so much headroom that the first thing to bind on a single host is the OS ephemeral-port
pool shared by the load generator and the gateway — a benchmark-control confound, quantified and
excluded.

---

## 2. Research Question

Phase 4 is **not** a "who is fastest?" benchmark. Two questions, kept in separate units:

**Closed-model question** — On one host / runtime / workload, how many concurrent streaming
requests can Platform-Thread+Queue, Virtual-Thread, and WebFlux each *hold* while keeping SLO
(TTFC p95 ≤ 2 s, stream-duration p95 ≤ 10 s) and reliability (every admitted request completes)?

**Open-model question** — Under sustained arrival traffic, up to what arrival rate can each
architecture keep serving *without accumulating backlog* and while keeping SLO and reliability?

Closed and Open results are **never** converted into one another or compared in a single unit.

---

## 3. Why Phase 4

Phases 1–3 repeatedly showed that changing the execution model changes the *shape* of overload
cost but does not make it disappear, and does not automatically improve throughput / CPU / RSS.
Phase 4 asks the next question directly: **where is the actual scalability boundary of each model**,
measured, not assumed — and separates a genuine model boundary from a test-environment ceiling.

---

## 4. Compared Architectures (final implementations)

| id | module | server model | outbound to Mock | key config |
|---|---|---|---|---|
| **M1** | `gateway-phase4-platform-queue` | Servlet async on Tomcat; chat task on a **fixed `ThreadPoolExecutor`** (`PT_WORKER_COUNT=50`) fronted by a bounded **`ArrayBlockingQueue`** (`PT_QUEUE_CAPACITY=500`), `AbortPolicy` | **blocking `java.net.HttpURLConnection`** (`BlockingMockLlmRelay` + `MockLlmClient`) | Tomcat `maxConnections=3200` (Open) |
| **M2** | `gateway-phase4-virtual-thread` | Servlet async on Tomcat; chat task on **`Executors.newVirtualThreadPerTaskExecutor()`** (one virtual thread per request) | **same blocking `HttpURLConnection`** — *byte-identical* `common/` package as M1 (SHA-256 verified) | Tomcat `maxConnections=3200` (Open) |
| **M3** | `gateway-phase4-webflux` | **Spring WebFlux** on Reactor Netty; fully reactive request handling | **Reactor Netty `WebClient`** with a bounded `ConnectionProvider` | `WEBCLIENT_MAX_CONNECTIONS = pendingAcquireMaxCount = 3200` (Open) |

**What is and is not a clean comparison:**
- M1 vs M2 isolates **one variable**: the executor that runs the chat task (platform pool+queue vs
  virtual-thread-per-task). Their outbound I/O path is *identical code*.
- M2 vs M3 changes **two things at once**: the request execution model *and* the outbound client
  (blocking `HttpURLConnection` vs pooled non-blocking `WebClient`). Differences between M2 and M3
  cannot be attributed to the thread model alone.

---

## 5. Common Runtime / Environment

- **Runtime:** Java 21 (Temurin 21.0.11+10, pinned), Spring Boot 4.1.0, Tomcat 11.0.22 (M1/M2),
  Reactor Netty (M3).
- **Host:** native macOS, Apple Silicon (arm64), 10 CPU cores, 16 GB RAM. No Rosetta, no Docker
  Desktop engine during measurement. AC power, sleep inhibited (`caffeinate`) for every run.
- **Mock LLM:** local FastAPI/uvicorn, `MAX_CONCURRENT_PROCESSING=0`, `MAX_WAITING=0` (unlimited —
  the gateway, never the Mock, is the object under test).
- **Ephemeral-port pool (host):** `net.inet.ip.portrange` 49152–65535 = **16384 ports**;
  `net.inet.tcp.msl` 15000 ms (TIME_WAIT = 2·MSL = 30 s). Never modified.

---

## 6. Workload Contract (frozen, identical for all cells)

Normal streaming SSE request: `firstChunkDelayMs=1000`, `chunkIntervalMs=200`, `chunkCount=35`,
`chunkSizeBytes=64` → nominal stream ≈ 1000 + 34·200 = **7800 ms**. `CHAT_TOTAL_TIMEOUT_MS=60000`.
Client lifecycle (Open, FINAL): explicit `client.close()` after each iteration reaches a terminal
state. k6 VU sizing (Open, FINAL): `PRE_ALLOCATED_VUS = RATE·60`, `MAX_VUS = ceil(PRE·1.25)`.
`dropped_iterations == 0` is mandatory for a run to be VALID.

---

## 7. Scalability Definitions

- **MSC (Maximum Sustainable Concurrency)** — highest concurrent-stream count at which the model is
  GREEN (reliability PASS + SLO PASS + no sustained backlog growth). Reported as a **bracket**
  [highest GREEN, first non-GREEN], never a single number.
- **MRC (Maximum Reliable Concurrency)** — highest concurrent-stream count at which every admitted
  request still completes (reliability PASS), even if SLO already fails. Bracket
  [highest reliability-PASS, first reliability-FAIL].
- **MSAR (Maximum Sustainable Arrival Rate)** — Open analogue of MSC: highest constant arrival rate
  that is GREEN. Bracket [highest GREEN, first non-GREEN].
- **CONTROL-CENSORED** — the model was still GREEN at the top of the control-valid range; its true
  boundary is **≥** that value and was **not found**. Never written as "= N" or "the maximum".
- **CONTROL-INVALID** — a run whose failure is caused by the test rig (loadgen / shared host / OS
  resource), not the model. Excluded from all model brackets and rankings; raw retained.

---

## 8. Experimental Methodology

Per axis: **Screening** (adaptive geometric points → GREEN/AMBER/RED, midpoint bracket refinement,
relative bracket width ≤ 20 % or ≤ 4 refinement points) → **Formal** (fixed cells, warmup/measure
window, N valid fresh replicates per cell, frozen deterministic run order, frozen confirmation
rule). Screening runs are **never** reused as Formal replicates.

Windows: Closed Formal wave 210 s (per closed protocol); **Open Screening 60 s + 120 s; Open Formal
120 s + 300 s** (single continuous k6 process, no warmup→measure restart).

Confirmation rule (identical vocabulary across Closed and Open Formal): initial n=3 →
**3/0 CONFIRMED** · **2/1 AMBIGUOUS** (→ exactly 2 extra reps) · **≤1/≥2 CONFIRMED UNSTABLE /
NOT CONFIRMED**. Final n=5 → **4/1 CONFIRMED** · **3/2 INCONCLUSIVE RANGE** · **2/3 CONFIRMED
UNSTABLE / NOT CONFIRMED**. GREEN-expected cells get "CONFIRMED SUSTAINABLE"; boundary-transition
cells get "CONFIRMED" / "NOT CONFIRMED".

Every Formal matrix records a SHA-256 provenance snapshot of all frozen files at start and
re-verifies zero drift at end; a lifetime-scoped `caffeinate -i -w <driver_pid>` runs for the whole
matrix. A **Stability Canary** (the cell with the most transport/connection pressure, GREEN
expected) runs once before each Formal matrix and must PASS.

---

## 9. Measurement Integrity / Validity

Phase 4's measurement-integrity process is itself a result. Every event below was investigated and
disclosed, not silently worked around; no raw artifact was ever deleted or re-run to change an
unwanted outcome.

1. **M2 R=160 Open "Virtual-Thread failure" → actually a Tomcat connector cap.** The first sustained
   Open load at R=160 failed; it looked like a VT execution ceiling. Forensics found Tomcat's
   unexamined default `maxConnections=8192` was the binding limit (`tomcat_connections_current`
   plateaued exactly at 8192). Reframed as `CONTROL_CONFIG_INVALID`, not a model result.
2. **Connector headroom raised → `EADDRNOTAVAIL`.** Raising `maxConnections` exposed a *second*,
   host-level ceiling: `java.net.BindException: Can't assign requested address` on outbound
   connections.
3. **Socket-telemetry audit → loopback double-count.** The socket sampler was matching the target
   port in *either* address column, double-counting every loopback connection (client socket +
   server-accepted socket). Fixed to be endpoint-aware (client-side only).
4. **xk6-sse v0.1.11 source audit → per-iteration transport leak.** `sse.open()` constructs a
   brand-new `http.Transport` per call and never reuses or closes it; its auto-cleanup closes only
   the response body, not the transport's idle-connection pool. Every completed iteration left an
   idle, un-reused connection open for the *server* to reclaim, multiplying the effective
   connection population (~8.25 connections per peak-concurrent VU).
5. **`client.close()` fix.** Capturing the client from the setup callback and calling `.close()`
   after each iteration reached a terminal state dropped the M2 R=160 organic Tomcat connection
   population from **~10 549 → ~1 276** — matching Little's Law on stream duration alone
   (160 × ~8 s ≈ 1280).
6. **R=256 two-leg diagnostic → shared-host ephemeral-port confound (Case B).** With the client
   lifecycle fixed, R=256 still failed. Endpoint-aware two-leg socket telemetry showed the
   k6→Gateway leg (~8282 unique ephemeral ports) and the Gateway→Mock leg (~8120) have **disjoint**
   port demands (A∩B ≈ 1) summing to **A∪B ≈ 16 362 of the host's 16 384-port pool**. Every
   `EADDRNOTAVAIL` / `BindException` second coincided with that ceiling; VT backlog, CPU (~1.4 of
   10 cores), platform threads, and the Tomcat connector were all non-binding. → **the load
   generator and the gateway, running on one host, exhaust one shared ephemeral-port pool.** This
   is a benchmark-control confound, not an M2 architecture boundary and not a Virtual-Thread
   finding.
7. **Single-host safe-range recalibration (Unit 8.2).** M2 sentinel, one run per rate, criteria
   frozen *before* the first calibration run. Ephemeral-port headroom criterion: `A∪B` peak
   **≤ 80 % of the 16 384-port pool** — the reciprocal of Phase 4's single frozen **1.25× headroom
   factor** (`phase4-resource-safety-policy.md` §4; the same factor already applied to M3's
   `WebClient` and the Tomcat connector). Result: R164 79.41 %, **R168 79.71 % → CONTROL_SAFE**;
   R176 84.90 %, R192 92.62 % → CONTROL_INVALID. → **`SAFE_OPEN_MAX_SINGLE_HOST_FINAL = 168`**.
8. **Open Formal Canary gate-instrumentation defect (disclosed).** The first Canary's substantive
   run was VALID/GREEN/transport-stable, but a gate check sampled host TIME_WAIT *at drain-end*
   (before the ~2·MSL decay) against ≤ 3000; the expected R=168 residue is ~10 k. Fix: **sample
   time only** moved to 75 s post-drain; **threshold unchanged (≤ 3000)**; missing sample →
   fail-closed. The replacement Canary PASSed (residue decayed to 0). Classified
   `CANARY GATE INSTRUMENTATION INVALID`, raw preserved, not a Formal replicate.
9. **Closed Formal F6-rep3 clock-integrity (prior, Unit 6.1).** A wall-clock-vs-Mock-monotonic
   duration mismatch → `ENVIRONMENT_CLOCK_INTEGRITY INVALID`; raw preserved, one protocol-consistent
   replacement rep — an invalid-measurement replacement, not a retry for a nicer number.

---

## 10. Closed Screening

M1's MSC and MRC brackets were discovered by screening and refined by midpoint bisection; M2/M3
were GREEN at every geometric point up to N=640 (no boundary). Extended screening (Phase 4.1) then
took M2/M3 GREEN through N=800/1000/1120 as well.

---

## 11. Closed Formal (`unit6-closed-formal`, N ≤ 640; median of 3 valid reps)

| cell | model | N | confirmation | colour 3× | TTFC p95 (s) | duration p95 (s) | platform threads | CPU (cores) | RSS peak | FD peak | executor active | queue depth | rejected |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 | M1 | 50 | **CONFIRMED SUSTAINABLE** | GREEN | 1.037 | 7.937 | 155 | 0.041 | 259 MiB | 119 | 50/50 | 0 | 0 |
| F2 | M1 | 56 | **CONFIRMED** | AMBER | **8.905** | **15.779** | 155 | 0.042 | 262 MiB | 125 | 50/50 | 6 | 0 |
| F3 | M1 | 350 | **CONFIRMED** | AMBER | 48.581 | 55.507 | 193 | 0.052 | 321 MiB | 419 | 50/50 | 300 | 0 |
| F4 | M1 | 400 | **CONFIRMED** | RED (`MODEL_TIMEOUT`) | 48.575 | 55.500 | 199 | 0.052 | 321 MiB | 469 | 50/50 | 350 | 0 |
| F5 | M2 | 640 | **CONFIRMED SUSTAINABLE** | GREEN | 1.139 | 8.204 | 228 | 0.113 | 384 MiB | 1301 | — | — | — |
| F6 | M3 | 640 | **CONFIRMED SUSTAINABLE** | GREEN | 1.152 | 8.142 | 30 | 0.112 | 260 MiB | 1326 | — | — | — |

All 6 cells: boundary predicate 3/3, expected signature 3/3, no extras, no STOP.

- **M1 Closed Formal MSC bracket = [50, 56]** (F1 sustainable, F2 not).
- **M1 Closed Formal MRC bracket = [350, 400]** (F3 every request completes; F4 first
  reliability failure — `MODEL_TIMEOUT`, **not** queue rejection: `executor_rejected_final = 0`
  even at N=400 with queue depth 350).

---

## 12. Closed Findings (N ≤ 640)

- **Executor capacity and service-quality boundary are not the same thing.** At N=50 all 50 workers
  are busy and the queue is empty → GREEN. At N=56 the queue holds only ~6 items, nothing is
  rejected, every request completes — but TTFC p95 has already jumped from 1.04 s to 8.9 s (SLO
  broken). The boundary is **queue-wait latency**, and it arrives while the queue is barely used.
- The queue never overflows to rejection in the tested range. At N=400 the queue sits at depth 350
  (of 500), still 0 rejections — the request's own 60 s absolute deadline fires first
  (`MODEL_TIMEOUT`). So M1's degradation ladder is: worker saturation → queue-wait SLO break
  (AMBER) → deadline timeout (RED), **never** `AbortPolicy` rejection at these N.
- **M2 / M3: no Closed boundary found at N ≤ 640.** Both CONFIRMED SUSTAINABLE, GREEN 3/3, latency
  indistinguishable from M1's stable point.

---

## 13. Extended Closed Findings (Phase 4.1, `unit6.7-extended-closed-formal`, N = 1120)

Control extended to `robust_clean_ceiling = 1400` → **`SAFE_CLOSED_MAX_EXTENDED = 1120`**
(1400 / 1.25). Extended screening: M2 & M3 GREEN at N = 800 / 1000 / 1120 — still no boundary.

| cell | model | N | confirmation | colour 3× | TTFC p95 (s) | duration p95 (s) | platform threads | CPU (cores) | RSS peak | FD peak |
|---|---|---|---|---|---|---|---|---|---|---|
| EF1 | M2 | 1120 | **CONFIRMED SUSTAINABLE** | GREEN | 1.286 | 8.417 | 279 | 0.146 | ~528 MiB | 2261 |
| EF2 | M3 | 1120 | **CONFIRMED SUSTAINABLE** | GREEN | 1.279 | 8.311 | 30 | 0.141 | ~294 MiB | 2286 |

- **M2 (Virtual Thread): MSC ≥ 1120, MRC ≥ 1120, CONTROL-CENSORED** — still not the architecture's
  true ceiling. Platform threads scale **sub-linearly** with virtual-task count: N=640 → 228,
  N=1120 → 279 (concurrency ×1.75, threads ×1.22).
- **M3 (WebFlux): MSC ≥ 1120, MRC ≥ 1120, CONTROL-CENSORED** — still not the true ceiling. Platform
  threads **completely flat at 30** regardless of N (event-loop model).
- **M2 vs M3 exact ceiling ranking: INCONCLUSIVE** — both censored; no arbitrary order assigned.

---

## 14. Open Control Calibration (`unit8.2`)

`SAFE_OPEN_MAX = 256` (the original Open screening cap) was **retired as historical / superseded** —
it validated the single-leg Direct-Mock path and the Tomcat connector, but never modelled the
two-leg same-host ephemeral-port constraint of the final workload. Recalibration (M2 sentinel, one
run/rate, criteria frozen before run 1):

| R | classification | `A∪B` peak / 16384 | note |
|---|---|---|---|
| 160 | control-safe (anchor) | ~77.5 % (est.) | Unit 7.5/8 known-clean; not re-run |
| **164** | CONTROL_SAFE | **79.41 %** | 0 EADDRNOTAVAIL |
| **168** | **CONTROL_SAFE** | **79.71 %** | 0 EADDRNOTAVAIL — **selected** |
| 176 | CONTROL_INVALID | 84.90 % | §4b headroom breach (no failure yet — clear pressure rise) |
| 192 | CONTROL_INVALID | 92.62 % | headroom breach |
| 256 | CONTROL_INVALID | 99.87 % | actual `EADDRNOTAVAIL` (Unit 8.1) — the original confound |

`A∪B` scales linearly at ≈ 79·R; `A∩B ≈ 1` at every rate (the two legs' ephemeral ports are always
disjoint, so their demands add). → **`SAFE_OPEN_MAX_SINGLE_HOST_FINAL = 168 req/s`** — the highest
CONTROL_SAFE calibration rate. The 80 % headroom line is the reciprocal of the project's frozen
1.25× headroom factor; it is **not** a post-hoc threshold.

---

## 15. Open Screening (`unit8-open-screening`, epoch `open-final-client-lifecycle-v1`)

| model | highest GREEN | first non-GREEN | refined MSAR bracket | reliability boundary | control-censored |
|---|---|---|---|---|---|
| **M1** | R6 | R7 (AMBER) | **[6, 7]** (rel. width 16.7 %) | in (7, 10] — R7 reliability PASS, R10 `MODEL_REJECTION` | no |
| **M2** | R168 | none ≤ 168 | — | not reached ≤ 168 | **yes** — MSAR ≥ 168 |
| **M3** | R168 | none ≤ 168 | — | not reached ≤ 168 | **yes** — MSAR ≥ 168 |

M1 refinement order: R7 (AMBER) → bracket [4,7]; R6 (GREEN) → bracket [6,7], width 16.7 % ≤ 20 %
→ stop (2 of 4 points). M1 R10 stays a **Screening secondary reliability observation**
(`MODEL_REJECTION`, completion_ratio 0.291). M2 R256 = **CONTROL-INVALID history**, excluded from
brackets. M2/M3 R2–R160 canonical points reused unchanged; R168 run as fresh ceiling-check points.

---

## 16. Open Formal (`unit9-open-formal`, 12 runs, 120 s + 300 s window; median of 3 valid reps)

Stability Canary (M2 R168) PASSed (attempt 2, after the disclosed gate fix). 12/12 valid, 0 retry,
0 extra, 0 STOP, 0 provenance drift. All 12: `dropped_iterations = 0`, `reliability_pass = true`,
`stall_detected = false`, `clock_integrity_ok = true`, `postflight_clean = true`,
0 `BindException`, 0 `EADDRNOTAVAIL`, TIME_WAIT 75 s post-drain ≤ 2.

| cell | model | R | confirmation | colour 3× | arrival /s | completion ratio | TTFC p95 (s) | duration p95 (s) | backlog trend (1st→2nd half) | platform threads | CPU (cores) | RSS peak | FD peak | model-specific |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 | M1 | 6 | **CONFIRMED SUSTAINABLE** | GREEN | 6.00 | 1.000 | 1.006 | 7.87 | 47 → 46 (flat) | 138 | 0.060 | 275 MiB | 108 | executor active 47/50, queue depth 0, rejected 0 |
| F2 | M1 | 7 | **CONFIRMED** | AMBER | 7.00 | 1.000 | **40.65** | **47.51** | **177 → 241 (rising)** | 138 | 0.062 | 350 MiB | 377 | executor active 50/50, queue depth 263, rejected 0 |
| F3 | M2 | 168 | **CONFIRMED SUSTAINABLE** | GREEN | 168.00 | 1.000 | 1.028 | 8.07 | 1338 → 1306 (declining) | 110 | 0.391 | 1159 MiB | 2756 | virtual_tasks_active 1370, Tomcat connections 1372/3200 |
| F4 | M3 | 168 | **CONFIRMED SUSTAINABLE** | GREEN | 168.00 | 1.000 | 1.022 | 8.06 | 1338 → 1303 (declining) | 30 | 0.328 | 450 MiB | 2791 | reactor active 1370/3200, pending 0–1 (transient) |

- **M1: Formal-confirmed Open MSAR bracket = [6, 7]** (F1 CONFIRMED SUSTAINABLE + F2 CONFIRMED,
  AMBER signature 3/3).
- **M2: Open MSAR ≥ 168 req/s, CONTROL-CENSORED** (F3 CONFIRMED SUSTAINABLE). Not "= 168".
- **M3: Open MSAR ≥ 168 req/s, CONTROL-CENSORED** (F4 CONFIRMED SUSTAINABLE). Not "= 168".
- **M2 vs M3 exact Open ceiling ranking: INCONCLUSIVE.**

---

## 17. Open Findings

- **M1 (R6 → R7): reliability held, sustainability broke.** At R7 every request still completes
  (2100/2100 each rep), zero executor rejection — but the 50/50-busy executor's queue grows to
  ~263, backlog rises through the whole 300 s window, and TTFC / stream-duration p95 blow out to
  **~40.6 s / ~47.5 s**. This is `MODEL_SATURATION` shape: *the SLO/sustainability boundary sits
  one step below the hard reliability/rejection boundary.* R10 Screening is `MODEL_REJECTION` RED —
  the hard boundary is somewhere in (7, 10].
- **This mirrors the Closed result conceptually** (SLO breaks while the queue is barely used;
  rejection/timeout comes much later) — but the two axes are **not** converted into one number.
- **M2 / M3: no Open boundary found ≤ R168.** Both GREEN 3/3 with backlog *declining* through the
  window, completion ratio 1.000, latency ≈ M1's stable point. The first thing to bind above R168
  on this rig is the shared-host ephemeral-port pool (Case B), which is a control confound.

---

## 18. Closed vs Open

Kept as **separate experiments**. Closed measures *concurrent ownership* (how many streams held at
once); Open measures *arrival sustainability* (what rate absorbed without backlog). They are not
combined into a single "capacity" number in this report. The one structural parallel worth stating:
in **both** axes, M1's first boundary is **queue-wait latency degradation** (AMBER) that arrives
well before any hard rejection/timeout; and in **both** axes, M2 and M3 are **control-censored** —
their real ceilings were not reached on this single host.

---

## 19. Resource Signatures

**Valid comparison only at equal offered load.** The only equal-load, both-sustaining comparison
point is **Open R168 (F3 vs F4)**:

| metric (median, R168) | M2 (Virtual Thread) | M3 (WebFlux) |
|---|---|---|
| platform threads (peak) | ~110 | ~30 |
| CPU (avg cores of 10) | 0.391 | 0.328 |
| RSS peak | ~1159 MiB | ~450 MiB |
| FD open peak | ~2756 | ~2791 |
| completion ratio | 1.000 | 1.000 |
| TTFC p95 / duration p95 | 1.028 s / 8.07 s | 1.022 s / 8.06 s |

At this specific rate / workload / runtime, M3 carries a smaller platform-thread and RSS footprint
than M2 while delivering the same throughput and latency. **This is a bounded observation, not a
law.** Forbidden generalisations: "WebFlux always uses 61 % less memory", "WebFlux is 3.6× more
efficient", "WebFlux sustains a higher maximum" (both ceilings are unknown). The Closed Extended
point (N=1120) shows the same *direction* (M2 ~528 MiB / 279 threads vs M3 ~294 MiB / 30 threads)
but at a different absolute load, so it is descriptive corroboration only.

M1's Open cells (R6, R7) are at a **different arrival rate** than M2/M3 (R168) — M1's resource
figures (138 threads, ~0.06 cores, 275–350 MiB) are **descriptive of its own operating point
only** and are not a resource comparison against M2/M3.

**TIME_WAIT / transport (Open Formal, drain-end residue):** M2 R168 ≈ **9600–9900**;
M3 R168 ≈ **6230–6250**. M3's lower residue is **consistent with** its pooled `WebClient` outbound
reusing connections, versus M2's `HttpURLConnection` path (which, per JDK source audit, calls
`connection.disconnect()` after every request and becomes a frequent active-closer against
`127.0.0.1:8000`). No causal proof beyond "consistent with"; no "WebFlux reduces TIME_WAIT by X %"
claim.

---

## 20. Failure Modes by Architecture (first limiting signature)

| model | first limiting signature (in the tested range) |
|---|---|
| **M1 (PT + queue)** | **Worker-pool saturation → queue-wait latency degradation → SLO break (AMBER).** Hard failure (`MODEL_TIMEOUT` on the 60 s deadline, or `MODEL_REJECTION` via `AbortPolicy` at higher Open rate) comes later; the queue never overflowed to rejection in the Closed Formal range. |
| **M2 (Virtual Thread)** | **No model limiter reached.** Within the control-valid range every internal resource had headroom (platform threads sub-linear, CPU ~1.4/10 cores at R256, VT scheduler / Tomcat connector / FD all non-binding). The first thing to bind is the **shared-host OS ephemeral-port pool** (Case B) — a test-rig confound, not the architecture. |
| **M3 (WebFlux)** | **No model limiter reached.** Platform threads flat at 30, CPU ~0.33 cores at R168, reactor connection pool non-binding (pending peak 0–1, transient). First-to-bind would also be the shared-host transport pool. |

---

## 21. H4 Hypothesis Verdicts (`phase4-design.md` §13, frozen wording)

| id | hypothesis (paraphrased from frozen wording) | verdict | evidence · caveat |
|---|---|---|---|
| **H4-a** | Is the MSC of PT-QUEUE / VT / WebFlux the same? | **NOT SUPPORTED** | M1 MSC = [50, 56] (found); M2/M3 MSC ≥ 1120 CONTROL-CENSORED (not found). They are not equal — M1 ≪ M2, M3. *Caveat:* M2/M3's true MSC is unknown, so the gap's size is a lower bound. |
| **H4-b** | In PT-QUEUE, how do executor-active saturation, queue depth/wait, and client TTFC relate as concurrency rises? | **SUPPORTED** | Monotone ladder: workers saturate first (50/50 from N=50 / R6), then added load goes to the queue, then **queue-wait — not rejection — breaks TTFC/duration SLO first** (N=50 GREEN 1.04 s → N=56 AMBER 8.9 s at queue depth 6; R6 1.0 s → R7 40.6 s at queue depth 263). Rejection/timeout is a later, separate boundary. |
| **H4-c** | In VT, does virtual-task concurrency grow JVM live platform-thread count 1:1? | **NOT SUPPORTED** | Sub-linear: Closed N=640 → 228 threads, N=1120 → 279 (×1.75 concurrency, ×1.22 threads); Open R168 (~1370 concurrent VTs) → ~110 threads. Not 1:1. |
| **H4-d** | In WebFlux, what platform-thread scaling curve vs concurrent-stream count? | **SUPPORTED (characterised)** | The curve is **constant**: 30 platform threads at N=640, N=1120, and Open R168 (~1370 streams) alike — independent of concurrency (event-loop model). |
| **H4-e** | What resource/failure signature appears at VT/WebFlux's first non-sustainable boundary? (no pre-assumed cause) | **NOT EVALUABLE (for the model boundary)** | No model boundary was reached for M2/M3 in the control-valid range (Closed censored at N=1120; Open censored at R=168). The only non-sustainable signature observed at higher Open rate was a **shared-host OS ephemeral-port exhaustion** (`EADDRNOTAVAIL`, Case B) — a benchmark-control confound, documented separately, not the architecture's signature. |
| **H4-f** | Is the MSC ranking the same as the MSAR ranking across the three models? | **PARTIALLY SUPPORTED** | Consistent as far as it can be checked: M1 is unambiguously lowest on **both** axes (MSC [50,56], MSAR [6,7], both found). M2 vs M3 is INCONCLUSIVE on **both** axes (control-censored on both). Nothing contradicts identical ranking, but 2 of 3 positions are unresolved on each axis, so "identical" cannot be fully confirmed. |
| **H4-g** | Does a higher MSC/MSAR necessarily mean lower CPU/RSS cost? | **NOT SUPPORTED** | The implication does not hold as a rule. At equal sustained Open load (R168) M3's footprint (~450 MiB / 30 threads) is much smaller than M2's (~1159 MiB / 110 threads) — but neither ceiling is known, so this cannot be read as "higher ceiling ⇒ lower cost". And Phases 2/3 already showed large platform-thread savings did **not** reduce CPU/RSS (VT: +18–27 % CPU vs PT; P3-A→P3-B: +27–32 % CPU). |

---

## 22. Important Bugs / Experimental Findings

- **xk6-sse v0.1.11 per-iteration transport leak** (§9.4/9.5) — a load-generator defect that
  inflated the measured connection population ~8×; fixed by an explicit `client.close()`.
- **Shared-host ephemeral-port confound (Case B)** (§9.6) — on a single host the load generator and
  the gateway draw from one 16384-port pool; their disjoint two-leg demands sum past it at R > 168.
  Quantified (`A∪B` ≈ 16 362, `A∩B` ≈ 1) and excluded from model results.
- **Tomcat `maxConnections` default (8192) is a silent binding ceiling** for sustained streaming
  Open load (§9.1) — the connector cap bound before any thread/CPU limit.
- **JDK `HttpURLConnection.disconnect()` semantics** (source-audited against Temurin 21.0.11+10):
  after a chunked SSE stream that the relay breaks on `event: final`, `disconnect()` neither
  guarantees immediate socket close nor prevents keep-alive reuse, and is **not** equivalent to
  `Connection: close`; for this workload it drives roughly one Gateway-initiated active-close per
  request against `127.0.0.1:8000`.

---

## 23. Limitations

- **M2 and M3 architecture ceilings were never found** — on this single host, Closed is censored at
  N = 1120 and Open at R = 168 req/s. Every M2/M3 result is a **lower bound**.
- **Single-host benchmark rig** — the load generator, gateway, Mock, and Prometheus share one
  machine and one OS ephemeral-port pool; above R = 168 that shared pool, not any architecture,
  binds.
- **One workload shape** (fixed 1000 ms first-chunk delay, 200 ms cadence, 35 chunks, 64 B). No
  variable payloads, no bursty arrival, no client cancellation storms, no failure-injection at
  scale.
- **M2 vs M3 changes two variables** (execution model + outbound client), so M2/M3 differences are
  not attributable to the thread model alone.
- **No CPU/RSS root-cause profiling** (JFR / async-profiler) — deferred, not run.
- Closed Formal wave window (210 s) differs from Open Formal (300 s) — comparisons across axes are
  qualitative only.

---

## 24. Threats to Validity

- **Instrumentation:** the socket sampler once double-counted loopback connections; the first
  Canary gate mis-timed a TIME_WAIT sample. Both were caught, disclosed, and corrected without
  touching measurement semantics; affected data was re-derived read-only or replaced by a
  protocol-consistent run.
- **Clock:** native macOS wall-clock discontinuities were seen once (Closed F6-rep3) — guarded by a
  wall-vs-monotonic cross-check that fails closed; the Open Formal driver carries the same guard
  (0 trips across 12 runs).
- **Control vs model attribution:** the entire Unit 7–8.2 arc exists to separate a rig limit from a
  model limit; the `SAFE_OPEN_MAX = 256 → 168` recalibration is the explicit correction.
- **Loadgen headroom:** k6 VU pool sized to `RATE·60` (the app's own 60 s deadline), not the
  nominal Little's-Law estimate, so a degrading model cannot be mistaken for a loadgen shortfall.
- **Replicate independence:** every Formal rep gets a fresh Gateway + Mock + Prometheus TSDB + k6;
  frozen deterministic run order distributes model/time-order bias; provenance hashed and
  re-verified zero-drift.

---

## 25. Practical Engineering Implications

- **No concurrency model is unconditionally best.** At equal admitted load the three were
  latency/throughput-indistinguishable; they differ in *where they stop scaling* and *what they
  cost per unit of headroom*.
- **A bounded pool + queue (M1) gives a predictable, early, and observable boundary** — worker
  saturation → queue-wait SLO break → deadline timeout. If you need a hard, understood ceiling and
  back-pressure, this is a feature.
- **Virtual threads (M2) let a blocking codebase hold far more concurrent work** (≥ 1120 concurrent
  streams, ≥ 168 req/s sustained here) with a sub-linear platform-thread footprint — but this is
  **not** infinite capacity and **not** independent of OS/transport resources. Admission control and
  connection-lifecycle discipline still matter (the `disconnect()`-per-request path is a real
  TIME_WAIT source).
- **WebFlux (M3) holds the same load with a flat ~30-thread footprint and roughly a third of M2's
  RSS at R = 168** — attractive when platform-thread or memory budget is the constraint — at the
  cost of a fully reactive programming model.
- **Choose against a checklist, not a benchmark headline:** is the outbound path blocking? expected
  peak concurrency and sustained arrival rate? is a bounded-overload (reject/timeout) policy
  required? memory / platform-thread / FD / connection budget? programming-model complexity your
  team can carry? operational observability of the boundary?
- **Benchmark on the topology you will run.** A shared-host load test hides the OS transport pool as
  a confound; a dedicated load-generator host is required to find M2/M3's real ceilings.

---

## 26. Phase 1 → 4 Research Story

- **Phase 1 (Java 8 Spring MVC baseline):** overload cost does not vanish under a bounded executor —
  it moves into queue wait and rejection.
- **Phase 2 (Platform vs Virtual Thread):** virtual threads cut the JVM platform-thread ownership
  cost 30–52 %, but did **not** proportionally cut RSS/CPU (CPU actually +18–27 %); admission
  control is still needed.
- **Phase 3 (Blocking MVC → MVC+WebClient → WebFlux):** changing the execution model moves the
  platform-thread footprint a lot (80 → 49 → 37 peak), but throughput/CPU/RSS do **not**
  automatically improve (CPU +27–32 % from P3-A→P3-B).
- **Phase 4:** so measure the actual scalability boundary directly, on two axes. **M1 (PT+queue)
  has a clear, found boundary** on both (MSC [50,56], MRC [350,400], MSAR [6,7]). **M2 (VT) and M3
  (WebFlux) have no boundary within the single-host control-valid range** (MSC/MRC ≥ 1120, MSAR
  ≥ 168, both CONTROL-CENSORED) — and the effort to prove that required separating a genuine model
  limit from a shared-host OS transport confound.

The through-line: **execution-model changes relocate and reshape cost; they do not delete it, and
they do not come with automatic resource savings — you have to measure where the wall actually is,
and make sure you are measuring the model and not the rig.**

---

## 27. Portfolio-safe Claims

Only these may be repeated outside this report:

- M1 (platform-thread + bounded queue): **Formal-confirmed Closed MSC [50, 56], MRC [350, 400];
  Open MSAR [6, 7] req/s.**
- M2 (virtual thread) & M3 (WebFlux): **Closed MSC/MRC ≥ 1120 concurrent streams; Open MSAR ≥ 168
  req/s — both CONTROL-CENSORED** (single-host control-valid range; true ceilings not found).
- **M2 vs M3 exact ceiling ranking: INCONCLUSIVE** (both censored).
- At equal sustained Open load (168 req/s): M3 ≈ 30 platform threads / ~450 MiB RSS vs M2 ≈ 110
  threads / ~1159 MiB RSS, same throughput and latency — **a bounded observation at this
  rate/workload/runtime, not a general efficiency ranking.**
- M1's Open boundary is **queue-wait latency**: at R = 7 every request completes with zero
  rejection, but TTFC/duration p95 blow out (~40 s / ~48 s) with rising backlog.

Not portfolio-safe: "VT/WebFlux does 168 RPS" (that's the rig ceiling, not the model), "WebFlux is
X× more efficient", "M2 = M3", any single "capacity" number combining Closed and Open.

---

## 28. Future Work (explicitly separated, not auto-started)

- **Optional Phase 4.2 — Extended Open Boundary on a separate load-generator host.** Removes the
  single-host shared ephemeral-port confound and searches for M2/M3's *actual* Open MSAR ceilings.
  A different environment epoch; does **not** replace or rebaseline Phase 4's results.
- **Optional CPU/RSS root-cause profiling** (JFR / async-profiler) for the M2-vs-M3 resource-cost
  gap. Not auto-run.

---

## 29. Canonical Artifact Index

| axis / stage | path |
|---|---|
| Closed Screening | `docs/test-results/phase4/unit5-closed-screening/` |
| Closed Formal (N ≤ 640) | `docs/test-results/phase4/unit6-closed-formal/` (`formal-aggregate.json`, `formal-summary-final.json`) |
| Closed Formal clock forensics | `docs/test-results/phase4/unit6.1-f6-rep3-pacing-forensics/` |
| Extended Closed control | `docs/test-results/phase4/unit6.5-extended-closed-control/` |
| Extended Closed Screening | `docs/test-results/phase4/unit6.6-extended-closed-screening/` |
| Extended Closed Formal (N = 1120) | `docs/test-results/phase4/unit6.7-extended-closed-formal/` (`extended-formal-summary.json`) |
| Open transport forensics (Case B) | `docs/test-results/phase4/unit8.1-m2-r256-two-leg-forensics/` |
| Open control recalibration | `docs/test-results/phase4/unit8.2-single-host-safe-open-max-recalibration/`; ADR `docs/decisions/phase4-open-single-host-ephemeral-headroom.md` |
| Open Screening (canonical) | `docs/test-results/phase4/unit8-open-screening/` (`UNIT8.3-COMPLETION.md`, `screening-summary-unit8.3.json`) |
| Open Formal | `docs/test-results/phase4/unit9-open-formal/` (`formal-aggregate.json`, `formal-summary.json`, `UNIT9-COMPLETION.md`) |
| Open Formal protocol | `docs/test-plan/phase4-open-formal-protocol.md` |
| Design / hypotheses | `docs/test-plan/phase4-design.md` §13 |
| Portfolio summary | `docs/portfolio/phase4-summary.md` |

Excluded from every canonical set: all `*-canary*` dirs, Screening runs (as Formal replicates),
Unit 8.1/8.2 diagnostics (as model results), any `-retry*` / discarded run.

---

## 30. Freeze Declaration

**Phase 4 is COMPLETE / FROZEN** as of this report. Canonical raw, aggregates, this Final Report,
and the Portfolio Summary are not modified further. Any future work (Phase 4.2 separate-host Open
boundary, profiling) writes to separate paths and does not overwrite Phase 4 canonical artifacts,
and is not auto-started. No new benchmark load, no profiling, no Phase 4.2, no Phase 5 is initiated
by this document.
