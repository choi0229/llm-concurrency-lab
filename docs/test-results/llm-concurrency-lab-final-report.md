# LLM Concurrency Lab — Phase 1 → 4 Whole-Project Final Report

**Status: FINAL.** Every number in this report is read read-only from a phase Final Report or a
canonical aggregate; none is written from memory. This document does not re-run, re-compute, or
modify any phase's raw data.

**Sources of truth (priority order):**

| # | source |
|---|---|
| 1 | `docs/test-results/phase{1,2,3}/phase{1,2,3}-final-report.md`; `docs/test-results/phase4/phase4-final-report.md` |
| 2 | Canonical aggregates: Phase 1 `unit6-formal-benchmark-results.md`; Phase 2 `unit6-native/_aggregated.json`; Phase 3 `unit7-formal/aggregate-result.json`; Phase 4 `unit6-closed-formal/formal-aggregate.json`, `unit6.7-extended-closed-formal/extended-formal-summary.json`, `unit9-open-formal/formal-aggregate.json` |
| 3 | Frozen ADRs / protocols: `docs/decisions/*.md`, `docs/test-plan/*-protocol.md` |
| 4 | Portfolio summaries `docs/portfolio/phase{1,2,3,4}-summary.md` |
| 5 | `README.md` |

**Cross-phase comparison rule (critical).** Each phase deliberately changed **one** thing and
used a **different runtime** to isolate it:

| phase | module(s) | runtime | the one variable isolated |
|---|---|---|---|
| 1 | `gateway-mvc-executor-java8` | Java 8, Spring Boot 1.5.22 | `ThreadPoolExecutor` configuration (core/max/queue/rejection) |
| 2 | `gateway-mvc-java21` | Java 21, Spring Boot 3.x | Chat executor: platform pool → virtual-thread-per-task |
| 3 | `gateway-mvc-blocking-spring5` / `-webclient` / `gateway-webflux` | Java 8, Spring Boot 2.7.18 | outbound client (blocking → WebClient), then server model (Servlet → WebFlux) |
| 4 | `gateway-phase4-{platform-queue,virtual-thread,webflux}` | Java 21, Spring Boot 4.1.0 | the scalability *boundary* of each of the three models |

**Therefore absolute numbers are NOT compared across phases.** What is compared across phases is
the **direction and shape** of each effect (does the thread count fall? does CPU/RSS follow?
where does overload land?). Phase 2's own report states this explicitly ("Java 8과 Java 21을 직접
비교하지 않는다").

---

## 1. Executive Summary

The lab started from a real production shape — a Spring Gateway that fronts an LLM's SSE stream
behind an async servlet and a dedicated executor — and asked one question that kept expanding:

> When a blocking SSE relay is put under load, **where does the cost actually go, and how far can
> each concurrency model carry it before it stops being sustainable?**

Four phases, each isolating one variable:

- **Phase 1** — With a bounded `ThreadPoolExecutor` behind the async servlet, overload cost does
  **not disappear**: it moves into queue wait, 503 rejection, platform-thread growth, or
  caller-thread back-propagation, depending on the executor configuration. Queue wait — not
  rejection — is what breaks client latency first (a natural experiment showed a **~9.6×** TTFC
  difference between two configs that differ only in whether a queue exists).
- **Phase 2** — Running the *same* blocking code on Java 21 virtual threads **decouples the JVM
  platform-thread count from load** (peak −30 % to −52 % vs the platform pool, and flat as load
  rises) — but that saving does **not** carry into RSS (mixed direction) or CPU (VT actually
  +18–27 %). Removing the admission limit does not add downstream capacity; it just relocates the
  backlog. Admission control is still required.
- **Phase 3** — Removing thread-per-request in stages (blocking outbound → WebClient → full
  WebFlux) drops the platform-thread peak predictably (**80 → 49 → 37**, −53.75 % end to end) with
  no change to throughput/latency — and again **CPU/RSS do not follow** (A→B CPU +27–32 %). The
  Phase 2 pattern reproduces at the server-model layer.
- **Phase 4** — So *where is each model's boundary?* Measured on two axes. The **platform-thread +
  bounded-queue** model has a clear, Formal-confirmed boundary on both:
  Closed **MSC [50, 56]**, **MRC [350, 400]**; Open **MSAR [6, 7] req/s** — and its SLO breaks via
  queue-wait latency long before anything is rejected. **Virtual-thread** and **WebFlux** reached
  **no boundary** inside the single-host control-valid range (Closed **MSC/MRC ≥ 1120**; Open
  **MSAR ≥ 168 req/s**, both **CONTROL-CENSORED**), and proving that required separating a genuine
  model limit from a shared-host OS ephemeral-port confound that had looked like a virtual-thread
  ceiling. **M2 vs M3 exact ranking: INCONCLUSIVE.**

**The one-line result of the whole lab:** *changing the execution model relocates and reshapes
overload cost and cuts the platform-thread footprint predictably; it does not delete the cost, it
does not automatically cut CPU or memory, and — for two of the three models — the real scalability
ceiling was never reached on a single host, so those numbers are lower bounds, not maxima.*

---

## 2. Phase 1 — Executor Concurrency (Java 8 Baseline)

**Question:** With an `AsyncContext` + dedicated `ThreadPoolExecutor` + `HttpURLConnection` SSE
relay, where is the LLM-streaming load actually absorbed?

**Design:** `Front → Spring MVC → AsyncContext → ThreadPoolExecutor → HttpURLConnection → FastAPI
SSE → relay`. Executor screening (closed model, configs D/E/A/B) then constant-arrival-rate formal
runs (open model) — warmup 2 min + measurement 5 min, **24 formal runs** (8 per config B/E/A).

**Key mechanics established:**

- **`AsyncContext` releasing the Tomcat request thread does not free the relay worker** — the
  worker stays occupied for the whole blocking read.
- **`ThreadPoolExecutor` order is `core → queue → max`**, not `core → max → queue` (a common
  misreading). Config D: `pool_size_peak=10` unchanged while `queue_size_peak=20` fills, and only
  at concurrency 40 does the pool grow to 20.
- **`HttpURLConnection` blocking read holds the worker past the absolute deadline.** Pre-screening
  probe: `onTimeout` fired at t ≈ 10.32 s, but the worker was still in `readLine()` until
  t ≈ 12.55 s (~2.24 s longer) — read timeout is a per-read relative timer, not an absolute
  deadline.

**Key findings:**

- Within each config's capacity (B ≈ 1.28 rps, E ≈ 6.41 rps) all three behave essentially
  identically. **Past capacity, each pays a different resource:**
  - **B** (bounded queue + `AbortPolicy`) — abandons `accept` (503). Reject 33.4 % at R2, 88.9 %
    at R12. Overload is *explicit* and the client can retry immediately.
  - **E** (`SynchronousQueue`, max 50) — grows platform threads with load
    (A-R12 `platform_thread_peak = 124`, RSS peak 310.9 MB — the largest of the three).
  - **A** (large queue + `CallerRunsPolicy`) — back-propagates overload onto Tomcat request
    threads (`nio-8080-exec-*`), confirmed by thread-name logs (A-R5 `caller_runs = 875`,
    A-R12 = 2975).
- **Queue wait causes the TTFC blow-up.** D vs E natural experiment (identical core/max, only
  difference = whether a queue exists): TTFC p95 **10.80 s vs 1.13 s** (~9.6×).
- Mock LLM was never the bottleneck (`mockllm_waiting_requests = 0` in all 45 runs).

**Hypothesis verdicts:** H1-a SUPPORTED · H1-a′ SUPPORTED · H1-b SUPPORTED · H1-c SUPPORTED ·
H1-c′ PARTIALLY SUPPORTED (confound) · H-timeout SUPPORTED.

**Phase 1 conclusion:** *"As long as blocking I/O is kept, the overload cost does not vanish — it
moves into queue wait / reject / platform-thread growth / caller-thread propagation."* This is the
starting point for Phase 2.

---

## 3. Phase 2 — Platform Thread vs Virtual Thread (Java 21)

**Question:** Running the same blocking I/O on Java 21 virtual threads — how much does it ease the
platform-thread ownership problem, and can it replace admission control?

**Design:** Same blocking programming model and `HttpURLConnection`; change **only the chat
executor** — platform `ThreadPoolExecutor` (P-E) vs `newVirtualThreadPerTaskExecutor()` +
`Semaphore(50)` admission gate (VT-Limited), at the same admission ceiling (= 50). Primary Formal:
R3/R6/R8 × 3 = **18/18 valid** (native macOS ARM64, after Docker Desktop was disqualified for
repeated environment-level stalls detected by two independent signals).

**Primary findings:**

- **Completion capacity / rejection curve / TTFC** — indistinguishable within replicate variance
  (R8 rejection 20.7 % vs 20.6 %). *(H2-a, H2-f SUPPORTED.)*
- **JVM platform-thread peak — the clean signal:** R3 **47 → 33**, R6 **71 → 35**, R8 **73 → 35**.
  VT-Limited stays flat and decoupled from load; P-E scales with it. Reduction **−29.8 % / −50.7 %
  / −52.1 %**. *(H2-b SUPPORTED.)*
- **RSS does NOT follow the thread saving:** R3/R6 VT RSS is actually *higher* (+5.7 % / +3.2 %),
  R8 slightly lower (−3.6 %) — no consistent direction. *(H2-c NOT SUPPORTED.)*
- **CPU does NOT fall either:** VT-Limited uses **+18–27 %** more avg CPU at all three loads
  (absolute < 0.06 core). *(H2-e NOT SUPPORTED.)*
- **Carrier pinning:** JFR `jdk.VirtualThreadPinned` — positive control B-0 detected intentional
  pinning 5/5, then normal SSE (B-1) and ~30 s blocking read (B-2) showed **0** events →
  **NOT OBSERVED UNDER TESTED CONDITIONS** (not generalised beyond this workload). *(H2-d.)*

**Secondary (VT-Unlimited) findings:**

- Gateway alone handled **500 concurrent streams** clean (0 failed/rejected); platform-thread peak
  did not track request count (96–128). *(S1 SUPPORTED.)*
- Removing the admission gate did **not** raise processing capacity: Mock `current_concurrency`
  peak stayed **20** either way; excess requests moved from Gateway reject to Mock's waiting queue,
  pushing TTFC to 16.7 s / duration to 23.6 s. *(S2 NOT SUPPORTED; S3 SUPPORTED.)*

**Phase 2 conclusion:** *Virtual threads decouple the JVM platform-thread count from load — that is
the real, clean effect. They do **not** automatically reduce CPU or memory, and they do **not**
replace admission control: downstream capacity, timeouts, fast-fail, and monitoring are still
required.*

---

## 4. Phase 3 — Blocking MVC vs WebClient vs WebFlux (Java 8, Spring Boot 2.7.18)

**Question:** Change the outbound client (blocking → WebClient) and then the server model (Servlet
→ WebFlux) — what changes in throughput / latency / thread / CPU / RSS?

**Design:** P3-A (MVC + `HttpURLConnection`), P3-B (MVC + WebClient), P3-C (WebFlux + WebClient).
Two controlled pairwise experiments — **A→B** changes only the outbound execution model, **B→C**
changes only the server response model. Same admission ceiling (`CHAT_ADMISSION_LIMIT = 50`),
R3/R7/R10 × 3 = **27/27 valid**.

**Key findings:**

- **Throughput, rejection, client TTFC, stream duration — near-identical across all three**
  implementations at every load (throughput diff < 0.2 %).
- **JVM platform-thread measurement-window peak: 80 → 49 → 37** — A→B **−38.75 %**, B→C **−24.49 %**,
  A→C **−53.75 %**, consistent at every load.
- **CPU / RSS do NOT move with the thread saving:** A→B CPU **+27–32 %**, RSS peak +5–9 %;
  B→C CPU −3–8 %, RSS load-dependent.

**Hypothesis verdicts:** H3-a … H3-f all **SUPPORTED** — including the explicit confirmation that
thread savings *do not imply* CPU/RSS savings (A→B CPU/RSS both went up).

**Phase 3 conclusion:** *Removing thread-per-request in stages reduces the platform-thread count
predictably and with no throughput/latency cost — but that reduction does not automatically
translate to CPU or memory savings. Phase 2's "thread saving ≠ resource saving" pattern reproduced
at a completely different layer (the Servlet/Reactive server model itself).*

---

## 5. Why Phase 4 Was Needed

Phases 1–3 all measured **resource footprint at a fixed load**. They answered "how many threads?"
but not the operational question that matters:

> The thread count is lower — fine. **How far can each model actually go before it stops being
> sustainable?**

Phase 4 defines and measures that boundary on two orthogonal axes:

- **Closed** — how many concurrent SSE streams can be *held* at once, with SLO (TTFC p95 ≤ 2 s,
  duration p95 ≤ 10 s) and reliability (every admitted request completes)? → **MSC** bracket, and
  **MRC** (reliability-only) bracket.
- **Open** — what constant arrival rate can be *sustained* without backlog growth, with SLO and
  reliability? → **MSAR** bracket.

Closed and Open are kept as separate experiments and never converted into one number.

---

## 6. Phase 4 — Closed (concurrent streams held; Java 21, Spring Boot 4.1.0)

Three models: **M1** (platform-thread pool 50 + bounded `ArrayBlockingQueue` 500 + blocking
`HttpURLConnection`), **M2** (virtual-thread-per-request + *byte-identical* blocking
`HttpURLConnection`), **M3** (WebFlux + Reactor Netty `WebClient`).

Screening → Formal (fixed cells, frozen run order, 3 valid reps/cell, frozen confirmation rule).

| cell | model | N | Formal result | key signature (median of 3) |
|---|---|---|---|---|
| F1 | M1 | 50 | **CONFIRMED SUSTAINABLE** (GREEN 3/3) | TTFC p95 1.04 s, executor 50/50 busy, **queue depth 0**, rejected 0 |
| F2 | M1 | 56 | **CONFIRMED** (AMBER 3/3) | TTFC p95 **8.9 s** (SLO broken), queue depth **6**, rejected 0 |
| F3 | M1 | 350 | **CONFIRMED** (AMBER 3/3) | reliability PASS, queue depth 300, rejected 0 |
| F4 | M1 | 400 | **CONFIRMED** (RED `MODEL_TIMEOUT` 3/3) | first reliability failure — **60 s deadline**, not rejection (`executor_rejected_final = 0` at queue depth 350) |
| F5 | M2 | 640 | **CONFIRMED SUSTAINABLE** (GREEN 3/3) | 228 platform threads, ~384 MiB RSS, 1301 FD |
| F6 | M3 | 640 | **CONFIRMED SUSTAINABLE** (GREEN 3/3) | **30** platform threads, ~260 MiB RSS, 1326 FD |

**Extended Closed (Phase 4.1)** — control extended to `SAFE_CLOSED_MAX_EXTENDED = 1120`; M2 & M3
screened GREEN at N = 800/1000/1120:

| cell | model | N | result | key signature (median of 3) |
|---|---|---|---|---|
| EF1 | M2 | 1120 | **CONFIRMED SUSTAINABLE** (GREEN 3/3) | 279 platform threads, ~528 MiB RSS, 2261 FD |
| EF2 | M3 | 1120 | **CONFIRMED SUSTAINABLE** (GREEN 3/3) | **30** platform threads, ~294 MiB RSS, 2286 FD |

**Closed conclusions:**

- **M1 Closed MSC bracket [50, 56]** (F1 sustainable, F2 not) — SLO breaks (TTFC 1.0 s → 8.9 s)
  while the queue holds only ~6 items and nothing is rejected. *Executor capacity and
  service-quality boundary are different things.*
- **M1 Closed MRC bracket [350, 400]** — every request still completes at N = 350 (queue depth
  300, 0 rejection); the first hard failure at N = 400 is the request's own 60 s deadline
  (`MODEL_TIMEOUT`), **never** `AbortPolicy` rejection at these N. M1's ladder is: worker
  saturation → queue-wait SLO break → deadline timeout.
- **M2 & M3: MSC ≥ 1120, MRC ≥ 1120 — CONTROL-CENSORED.** No architecture boundary found.
  M2's platform threads scale **sub-linearly** (N=640 → 228, N=1120 → 279); M3's stay **flat at 30**
  regardless of N.
- **M2 vs M3 exact ceiling ranking: INCONCLUSIVE** (both censored).

`1120` and `640` are **not** written as maxima.

---

## 7. Phase 4 — Open (sustained arrival rate)

Same three models, canonical Open harness, control ceiling
`SAFE_OPEN_MAX_SINGLE_HOST_FINAL = 168 req/s` (see §9-H). Open Formal: 12 runs, 120 s + 300 s
window, frozen order, 3 valid reps/cell.

| cell | model | R | Formal result | key signature (median of 3) |
|---|---|---|---|---|
| F1 | M1 | 6 | **CONFIRMED SUSTAINABLE** (GREEN 3/3) | completion ratio 1.000, TTFC p95 1.006 s, duration p95 7.87 s, executor 47/50, **queue depth 0**, backlog flat |
| F2 | M1 | 7 | **CONFIRMED** (AMBER 3/3, AMBER signature 3/3) | **completion ratio 1.000, 0 rejection, reliability PASS** — but TTFC p95 **40.65 s**, duration p95 **47.51 s**, backlog **rising** (177 → 241), executor 50/50, queue depth **263** |
| F3 | M2 | 168 | **CONFIRMED SUSTAINABLE** (GREEN 3/3) | completion ratio 1.000, TTFC p95 1.028 s, duration p95 8.07 s, backlog **declining** (1338 → 1306), Tomcat conn 1372/3200 non-binding |
| F4 | M3 | 168 | **CONFIRMED SUSTAINABLE** (GREEN 3/3) | completion ratio 1.000, TTFC p95 1.022 s, duration p95 8.06 s, backlog **declining** (1338 → 1303), reactor pool non-binding (pending 0–1 transient) |

**Open conclusions:**

- **M1 Formal-confirmed Open MSAR bracket [6, 7] req/s.** At R = 7 every request completes with
  zero rejection — yet backlog grows through the whole 300 s window and TTFC / duration p95 blow
  out to ~40 s / ~48 s. **100 % completion is not the same as sustainable.** The hard
  reliability/rejection boundary (`MODEL_REJECTION` at R = 10 in Screening) sits above the
  SLO/sustainability boundary.
- **M2: Open MSAR ≥ 168 req/s — CONTROL-CENSORED.** **M3: Open MSAR ≥ 168 req/s — CONTROL-CENSORED.**
- **M2 vs M3 exact Open MSAR ranking: INCONCLUSIVE.**

`168` is **not** written as a model maximum — it is the single-host benchmark rig's transport-safe
ceiling.

---

## 8. Same-Load M2 vs M3 Comparison (Open R = 168, the one equal-load both-sustaining point)

| metric (median of 3, Open R168) | M2 (Virtual Thread) | M3 (WebFlux) |
|---|---|---|
| completion throughput | ~168 /s | ~168 /s |
| completion ratio | 1.000 | 1.000 |
| TTFC p95 | 1.028 s | 1.022 s |
| stream duration p95 | 8.07 s | 8.06 s |
| JVM platform threads (peak) | **~110** | **~30** |
| CPU (avg cores of 10) | 0.391 | 0.328 |
| RSS peak | **~1159 MiB** | **~450 MiB** |
| FD open peak | ~2756 | ~2791 |
| TIME_WAIT residue at drain-end | ~9600–9900 | ~6230–6250 |

**Reading:** at the same offered load, the same throughput and latency, M3 carries a markedly
smaller platform-thread and RSS footprint than M2. M3's lower TIME_WAIT residue is **consistent
with** its pooled `WebClient` reusing connections versus M2's `HttpURLConnection` path (which,
per JDK source audit, calls `disconnect()` after every request). **Bounded observation, not a
law** — see §17 for what may not be generalised.

---

## 9. Methodological Findings (the project's strongest asset)

| # | finding | what it was, and what it changed |
|---|---|---|
| **A** | `ThreadPoolExecutor` order | The intuitive `core → max → queue` is wrong; it is `core → queue → max`. Phase 1 Unit 5 reproduced the documented behaviour and it became a load-point design constraint. |
| **B** | JFR positive control first | Before judging virtual-thread pinning, Phase 2 verified the `jdk.VirtualThreadPinned` detector on an intentional-pinning workload (5/5) — so "0 events on the real Gateway" is a measurement, not a blind spot. |
| **C** | Clock-integrity cross-check | Phase 4 Closed F6-rep3 showed a wall-clock-vs-Mock-monotonic-duration mismatch → `ENVIRONMENT_CLOCK_INTEGRITY INVALID`; raw preserved, one protocol-consistent replacement rep. Not a retry for a nicer number. |
| **D** | M2 R = 160 "virtual-thread ceiling" | Looked like a VT execution limit; forensics found **Tomcat `maxConnections = 8192` default** binding (`tomcat_connections_current` plateaued exactly at 8192). Reframed `CONTROL_CONFIG_INVALID`. |
| **E** | xk6-sse v0.1.11 connection-lifecycle bug | Source audit: `sse.open()` builds a new `http.Transport` per call and never closes it; auto-cleanup closes only the response body. Every completed iteration leaked an idle connection for the *server* to reclaim (~8.25 per peak VU). Fixed with explicit `client.close()` → **M2 R160 connection population ~10 549 → ~1 276** (matches Little's Law, 160 × 8 s ≈ 1280). |
| **F** | Socket-sampler double-count | The sampler matched the target port in *either* address column → every loopback connection counted twice (client socket + server-accepted socket). Fixed to endpoint-aware (client-side only). |
| **G** | Shared-host ephemeral-port confound (Case B) | Two-leg endpoint-aware telemetry at R = 256: k6→Gateway (~8282 unique ephemeral ports) and Gateway→Mock (~8120) have **disjoint** port demands (A∩B ≈ 1) summing to **A∪B ≈ 16 362 of the host's 16 384-port pool** → `EADDRNOTAVAIL` while every JVM/CPU/connector resource had headroom. The load generator and the gateway on one host exhaust one shared OS pool. **A benchmark-control boundary, not an architecture boundary.** |
| **H** | SAFE_OPEN_MAX recalibration | `256 → 168`. Criterion frozen *before* the calibration ran: `A∪B` peak ≤ **80 % of the pool** — the reciprocal of the project's single frozen **1.25× headroom factor**. R164 79.41 %, **R168 79.71 % CONTROL_SAFE**; R176 84.90 %, R192 92.62 % CONTROL_INVALID. |
| **I** | Open Formal Canary gate defect | The first Canary's run was VALID/GREEN, but a gate check sampled host TIME_WAIT *at drain-end* (before the ~2·MSL decay) against ≤ 3000; the expected R168 residue is ~10 k. Fix: **sample time only** moved to 75 s post-drain; **threshold unchanged (≤ 3000)**; fail-closed on a missing sample. Classified `CANARY GATE INSTRUMENTATION INVALID`, raw preserved, replacement Canary PASSed. |

Common thread: **every anomaly was investigated to root cause and disclosed; no raw run was
deleted or re-run to produce a nicer number; a rig limit was never allowed to stand as a model
result.**

---

## 10. Core Findings (whole project, ≤ 5)

1. **Async / non-blocking / virtual threads do not remove overload cost** — they relocate it
   (queue wait, rejection, caller-thread propagation, downstream backlog) and reshape it.
2. **For a platform-thread pool + bounded queue, queue-wait latency breaks service quality long
   before the queue overflows to hard failure** — the SLO/sustainability boundary sits below the
   reliability/rejection boundary (Closed MSC [50,56] vs MRC [350,400]; Open MSAR [6,7] with 100 %
   completion at R = 7).
3. **Virtual threads keep blocking code and decouple the JVM platform-thread count from load**
   (peak −30–52 % vs a platform pool, flat as load rises) — but this is not infinite capacity and
   not free.
4. **WebFlux holds high concurrency with a near-constant ~30-thread footprint** (independent of
   concurrency) and, at equal sustained Open load, roughly a third of the virtual-thread model's
   RSS.
5. **A higher concurrency ceiling does not guarantee lower CPU/RSS**, and the *actual* architecture
   ceiling must be separated from the *benchmark/control* ceiling — for virtual threads and WebFlux
   on this single host, the real ceiling was never reached (all their numbers are lower bounds).

---

## 11. Final Architecture Comparison

| dimension | M1 — Platform pool + bounded queue | M2 — Virtual thread per request | M3 — WebFlux + WebClient |
|---|---|---|---|
| programming model | blocking, imperative | blocking, imperative | reactive |
| outbound I/O | blocking `HttpURLConnection` | blocking `HttpURLConnection` (identical code to M1) | pooled non-blocking `WebClient` |
| request ownership | one platform worker (of 50) per active relay | one virtual thread per request | event loop, no per-request thread |
| queue / admission | explicit bounded `ArrayBlockingQueue` (500) + `AbortPolicy` | none in M2 (Phase 2 used a `Semaphore` gate) | reactor `ConnectionProvider` bound (outbound) |
| platform threads (qualitative) | grow with concurrency | **sub-linear** with concurrency | **flat** (~30), concurrency-independent |
| overload / backpressure point | worker saturation → queue-wait latency → deadline timeout / rejection | not reached in the control-valid range | not reached in the control-valid range |
| first failure signature | queue-wait SLO break (AMBER) → `MODEL_TIMEOUT` / `MODEL_REJECTION` | none (rig's OS ephemeral-port pool binds first) | none (rig's OS ephemeral-port pool binds first) |
| operational complexity | lowest — one pool, one queue, one explicit policy | low — blocking code retained; connection-lifecycle discipline matters | highest — reactive debugging / observability |
| best-fit workload | when a hard, understood, back-pressured ceiling is required | a large blocking-I/O codebase that must hold far more concurrency without a rewrite | platform-thread- or memory-budget-constrained, team can carry reactive |

*Numbers are in §12; the rows above are qualitative claims kept separate from them.*

---

## 12. Final Boundary Table

| Architecture | Closed MSC | Closed MRC | Open MSAR |
|---|---|---|---|
| **M1** (Platform pool + queue) | **[50, 56]** | **[350, 400]** | **[6, 7] req/s** |
| **M2** (Virtual thread) | **≥ 1120** — CONTROL-CENSORED | **≥ 1120** — CONTROL-CENSORED | **≥ 168 req/s** — CONTROL-CENSORED |
| **M3** (WebFlux) | **≥ 1120** — CONTROL-CENSORED | **≥ 1120** — CONTROL-CENSORED | **≥ 168 req/s** — CONTROL-CENSORED |

All M1 brackets are Formal-confirmed (3/3). Every M2/M3 cell is a **lower bound**: the model was
still GREEN at the top of the control-valid range and its true ceiling was not found. **M2 vs M3
exact ranking: INCONCLUSIVE** on all three columns. `168` = `SAFE_OPEN_MAX_SINGLE_HOST_FINAL` (the
single-host rig's transport-safe ceiling); `1120` = `SAFE_CLOSED_MAX_EXTENDED`.

---

## 13. Closed vs Open

They measure different things and are **not divided into one number**:

- **Closed** — how many active streams can be *held at one instant*.
- **Open** — how well a *continuous arrival stream* is kept up with, without accumulating backlog.

M1 makes the distinction concrete: at Open R = 7 it has **100 % one-shot completion and zero
rejection** (a high reliability capacity), yet it is **not sustainable** — backlog grows and
latency blows out. High reliability-under-load ≠ steady-state sustainability.

---

## 14. Phase 1 → 4 Hypothesis / Question Evolution

| phase | initial expectation | experiment | finding | next question |
|---|---|---|---|---|
| **1** | "An async servlet frees the thread, so concurrency is handled." | 24 formal runs across 3 executor configs (open model) | The async servlet frees the *Tomcat* thread, not the relay worker. Overload moves to queue wait / reject / caller thread. **Queue wait breaks TTFC first (~9.6×).** | If blocking I/O is the problem, does removing platform-thread ownership fix it? |
| **2** | "Virtual threads remove the thread bottleneck — problem solved." | P-E vs VT-Limited, 18/18 valid; VT-Unlimited screening; JFR pinning diagnostic | Platform-thread count **decoupled from load** (−30–52 %), but **RSS/CPU do not follow** (CPU +18–27 %). Unlimited admission just relocates the backlog downstream. **Admission control still needed.** No observable pinning. | If reactive removes thread-per-request entirely, do all resources improve? |
| **3** | "Full reactive is faster and lighter across the board." | P3-A → P3-B → P3-C, 27/27 valid, two pairwise experiments | Platform threads **80 → 49 → 37** (−53.75 %) with no throughput/latency change — but **CPU/RSS again do not follow** (A→B CPU +27–32 %). | Given the footprints, **where is each model's actual scalability boundary?** |
| **4** | "Measure the maximum concurrency / rate of each model." | Closed + Open Screening + Formal; Extended Closed; two-leg transport forensics; control recalibration | **M1 has a found boundary on both axes** (MSC [50,56], MRC [350,400], MSAR [6,7]). **M2/M3 have none in the control-valid range** (≥ 1120, ≥ 168 req/s, CONTROL-CENSORED) — after separating a **shared-host OS ephemeral-port confound** from a model limit. | (out of scope) A separate load-generator host to find M2/M3's real Open ceilings — Phase 4.2, optional. |

---

## 15. Practical Engineering Decision Guide

**No model is unconditionally best.** At equal admitted load, all three were
latency/throughput-indistinguishable in every phase. Choose against a checklist:

### Platform thread + bounded queue
- **For:** simplest to reason about; bounded resource use; overload policy is explicit and
  observable (503 / timeout); the boundary is early, predictable, and back-pressured.
- **Watch:** queue-wait latency degrades service quality well before rejection; worker count and
  queue depth must be sized to the SLO, not just to "not rejecting".

### Virtual thread per request
- **For:** keep a large blocking-I/O codebase unchanged while holding far more concurrent work;
  platform-thread footprint grows sub-linearly.
- **Watch:** downstream capacity is unchanged — admission control, timeouts, fast-fail, and
  monitoring are still required; RSS/CPU do not fall with the thread count (CPU may rise); the
  outbound connection lifecycle (`disconnect()`-per-request) is a real TIME_WAIT source.

### WebFlux + WebClient
- **For:** high concurrent-connection handling with a flat, small platform-thread footprint and
  (at the one equal-load point measured) roughly a third of the virtual-thread model's RSS.
- **Watch:** reactive programming, debugging, and observability complexity; CPU/RSS are not
  guaranteed lower than the alternatives; a pooled outbound `ConnectionProvider` must be sized.

**Do not conclude "always WebFlux" (or "always virtual threads").**

---

## 16. Portfolio-safe Claims

- "Compared Platform-Thread+Queue, Virtual-Thread, and WebFlux LLM-SSE gateway architectures on a
  fixed Java 21 runtime under Closed and Open load, and verified service-quality / reliability /
  arrival-sustainability boundaries with Formal (replicated, confirmation-ruled) benchmarks."
- "Platform-thread + bounded-queue: Formal-confirmed **Closed MSC [50, 56], MRC [350, 400], Open
  MSAR [6, 7] req/s**; its SLO breaks via **queue-wait latency** while the queue is barely used and
  nothing is rejected."
- "Virtual-thread and WebFlux showed **no boundary** within the single-host control-valid range —
  **≥ 1120 concurrent streams, ≥ 168 req/s, both CONTROL-CENSORED** (lower bounds, not maxima)."
- "At equal sustained Open load (168 req/s), Virtual-Thread and WebFlux held the same
  throughput/latency while WebFlux ran at **~30 vs ~110** platform-thread peak and **~450 vs
  ~1159 MiB** RSS — a bounded observation at this rate/workload/runtime, not a general efficiency
  ranking."
- "Across Phases 2–3, cutting the platform-thread footprint (−30–52 %, then 80 → 37) produced
  **no** matching CPU/RSS reduction."
- "Isolated a **shared-host OS ephemeral-port confound** that had presented as a virtual-thread
  scheduler ceiling, and reported the two models as control-censored rather than overclaiming a
  maximum."

Every `≥` keeps its **lower-bound / CONTROL-CENSORED** meaning.

---

## 17. What NOT to Claim

- ✗ "Virtual thread max concurrency = 1120" / "WebFlux max concurrency = 1120"
- ✗ "Virtual thread max throughput = 168 req/s" / "WebFlux max throughput = 168 req/s"
- ✗ "WebFlux is faster than Virtual Threads" (equal throughput/latency at every measured point)
- ✗ "WebFlux always uses 61 % less memory" / "WebFlux is 3.6× more efficient"
- ✗ "Virtual Threads reduce CPU" (Phase 2: +18–27 %)
- ✗ "M2 = M3" / "the Virtual-Thread ceiling equals the WebFlux ceiling"
- ✗ Any single "capacity" number combining Closed and Open
- ✗ Absolute numeric comparison of a Phase 1 value against a Phase 2/3/4 value (different runtimes)

---

## 18. Whole-Project Limitations

- **Single host, loopback**, one machine shared by load generator + gateway + Mock + Prometheus;
  above Open R = 168 the shared OS ephemeral-port pool binds, not any architecture.
- **M2 / M3 architecture ceilings never found** — Closed censored at N = 1120, Open at R = 168.
  Every M2/M3 figure is a lower bound.
- **Mock LLM, not a real model** — fixed timing, unlimited capacity by design (the gateway is the
  object under test).
- **One SSE workload shape** (1 s first chunk, 200 ms cadence, 35 chunks, 64 B), fixed 60 s
  timeout. No variable payloads, bursty arrival, cancellation storms, or large-scale failure
  injection.
- **Different runtime per phase** (Java 8/1.5.22, Java 21/3.x, Java 8/2.7.18, Java 21/4.1.0) — by
  design, to isolate one variable per phase; **absolute cross-phase numeric comparison is not
  valid**, only direction/shape.
- **No CPU/RSS root-cause profiling** (JFR / async-profiler) — deferred.
- **Closed and Open windows differ** (Phase 4 Closed wave 210 s vs Open Formal 300 s) — cross-axis
  comparison is qualitative only.

---

## 19. Future Work (explicitly separated; not started, not auto-started)

- **Phase 4.2 — Extended Open Boundary on a separate load-generator host.** Removes the single-host
  shared ephemeral-port confound and searches for M2/M3's *actual* Open MSAR ceilings. A different
  environment epoch; does not replace or rebaseline any Phase 1–4 result.
- **CPU/RSS root-cause profiling** (JFR / async-profiler) for the M2-vs-M3 resource-cost gap and
  the Phase 2/3 "thread saving ≠ resource saving" pattern.

---

## 20. Résumé Candidates

**한국어:**
> Java 21 기반 LLM SSE Gateway에서 Platform Thread+Queue·Virtual Thread·WebFlux 세 동시성 구조를
> Closed(동시 stream 보유량)/Open(지속 arrival rate) 부하로 비교하고, 서비스 품질·reliability·
> queue latency·thread/CPU/RSS를 Formal Benchmark(반복·confirmation rule)로 계측. Platform
> Thread+Queue의 경계를 확정하고(MSC [50,56], MRC [350,400], MSAR [6,7] req/s), Virtual Thread/
> WebFlux는 single-host control 범위 내에서 경계 미발견(≥1120 stream, ≥168 req/s, CONTROL-CENSORED)
> 으로 보고. Virtual Thread 한계로 보이던 실패를 shared-host ephemeral-port 문제로 규명해
> architecture 한계와 benchmark 한계를 분리.

**English:**
> Benchmarked platform-thread-pool vs virtual-thread vs WebFlux LLM-SSE gateway scalability on a
> fixed Java 21 runtime across two axes (concurrent-stream capacity, sustained arrival rate);
> Formal-confirmed the thread-pool model's boundary (MSC [50,56], MRC [350,400], MSAR [6,7] req/s)
> and reported the other two as control-censored lower bounds (≥ 1120 streams, ≥ 168 req/s) after
> isolating a shared-host OS ephemeral-port confound that had masqueraded as a virtual-thread
> ceiling.

---

## 21. Interview Story (Problem → Hypothesis → Experiment → Surprise → Forensics → Revision → Conclusion)

- **Problem.** A Spring Gateway relaying an LLM's SSE stream, behind an async servlet + a
  dedicated `ThreadPoolExecutor` + a blocking `HttpURLConnection`. Under load it degrades — where
  does the cost actually go?
- **Hypothesis (Phase 1).** The async servlet frees the thread, so the executor config shouldn't
  matter much.
- **Experiment.** 24 formal open-model runs across bounded-queue / `SynchronousQueue` /
  `CallerRunsPolicy` configs.
- **Surprise.** The configs are identical within capacity, but past it they pay *completely
  different* resources — and a two-config natural experiment (identical core/max, only a queue
  differs) showed a **9.6× TTFC gap**. Queue wait, not rejection, is the first thing to break.
- **Revision → Phase 2/3.** Try removing the platform-thread ownership: virtual threads
  (Phase 2), then reactive (Phase 3). Both cut the thread count a lot (−52 %, then 80 → 37) — but
  **CPU and RSS didn't follow**, and virtual threads without an admission gate just moved the
  backlog downstream.
- **Phase 4 — the debugging story worth telling.** Trying to find the virtual-thread model's Open
  ceiling, R = 160 failed and it looked like a virtual-thread scheduler limit. Forensics found
  **Tomcat's default `maxConnections = 8192`** was binding. Raising it exposed `EADDRNOTAVAIL`.
  Auditing the load generator's source found **xk6-sse leaks a transport connection per iteration**
  (fixed → connection population 10.5k → 1.28k). At R = 256 it still failed — two-leg socket
  telemetry showed the load generator and the gateway, on one host, **share one 16 384-port
  ephemeral pool and together demand ~16 362 of it**. So the "virtual-thread ceiling" was three
  layers of test-rig artifact. Recalibrated the safe rate 256 → 168 with a headroom rule frozen
  *before* the calibration ran.
- **Conclusion.** The platform-thread + queue model has a real, found, early, back-pressured
  boundary. Virtual threads and WebFlux had so much headroom that the single-host test rig — not
  the architecture — was the limit, so those results are reported as **control-censored lower
  bounds**, and the exact virtual-thread-vs-WebFlux ranking as **INCONCLUSIVE**, not fudged into a
  winner. The reusable lesson: *measure where the wall is, and make sure you are measuring the
  model and not the rig.*

---

## 22. Whole-Project Final Readiness & Freeze Recommendation

- All four phases are COMPLETE and FROZEN with their own Final Reports and canonical aggregates.
- This whole-project report is read-only over those and adds no new measurement.
- **Recommendation: declare the LLM Concurrency Lab (Phase 1 → 4) COMPLETE / FROZEN.** Future work
  (Phase 4.2 separate-host Open boundary, profiling) writes to separate paths, does not overwrite
  any phase's canonical artifacts, and is not auto-started. No new benchmark load, no profiling,
  no Phase 4.2, no Phase 5 is initiated by this document.
