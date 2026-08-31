# Phase 4 — Portfolio Summary

Every claim here is proven in
[`docs/test-results/phase4/phase4-final-report.md`](../test-results/phase4/phase4-final-report.md)
and its canonical aggregates. No claim is added here that the Final Report does not establish.

---

## A. The problem

"Virtual threads scale better than a thread pool" and "WebFlux is more efficient" are common
claims, but they're usually backed by throughput graphs, not by *where each model actually stops
being sustainable*. Phase 4 measures that boundary directly, for three server concurrency
architectures on one fixed runtime, host, and streaming (SSE) workload, along two independent axes:

- **Closed** — how many concurrent streams can be *held* at once (SLO + reliability intact)?
- **Open** — what constant arrival rate can be *sustained* without backlog growth?

Architectures:

- **M1** — Platform thread + fixed `ThreadPoolExecutor` (50) + bounded `ArrayBlockingQueue` (500) +
  blocking `HttpURLConnection`.
- **M2** — Virtual thread per request + the *same* blocking `HttpURLConnection` (byte-identical
  outbound code to M1).
- **M3** — Spring WebFlux + Reactor Netty `WebClient` (pooled, non-blocking).

---

## B. Experimental design

- Common runtime: Java 21 (Temurin 21.0.11+10), Spring Boot 4.1.0, native macOS arm64, 10 cores /
  16 GB, Docker off, AC power, sleep inhibited.
- Fixed SSE workload (1 s first chunk, 200 ms cadence, 35 chunks, 64 B) → ~7.8 s nominal stream.
  Mock LLM unlimited (the gateway is the object under test).
- **Screening** (adaptive geometric points + midpoint bracket bisection) → **Formal** (fixed cells,
  frozen run order, 3 valid replicates per cell, frozen confirmation rule 3/0 → CONFIRMED,
  2/1 → AMBIGUOUS + 2 extra, etc.). Screening runs are never reused as Formal replicates.
- Open Formal window: 120 s warmup + 300 s measurement per run; 12 runs in a frozen balanced order;
  every run gets a fresh gateway + Mock + Prometheus + k6; SHA-256 provenance of all frozen files
  verified zero-drift end-to-end.
- A one-shot **Stability Canary** (highest-pressure cell, GREEN expected) gates each Formal matrix.

---

## C. Key results

### Closed (concurrent streams held)

| model | Formal result |
|---|---|
| **M1** | **MSC bracket [50, 56]** (GREEN → AMBER) · **MRC bracket [350, 400]** (every request completes → first `MODEL_TIMEOUT`). Both Formal-confirmed 3/3. |
| **M2 (VT)** | **MSC ≥ 1120, MRC ≥ 1120 — CONTROL-CENSORED.** No boundary found in the control-valid range. |
| **M3 (WebFlux)** | **MSC ≥ 1120, MRC ≥ 1120 — CONTROL-CENSORED.** No boundary found. |

### Open (sustained arrival rate)

| model | Formal result |
|---|---|
| **M1** | **MSAR bracket [6, 7] req/s** (Formal-confirmed). At R = 7 every request completes with **zero rejection**, but backlog rises through the run and TTFC / stream-duration p95 blow out to **~40 s / ~48 s** (SLO broken). |
| **M2 (VT)** | **MSAR ≥ 168 req/s — CONTROL-CENSORED.** GREEN 3/3 at the rig's transport-safe ceiling. |
| **M3 (WebFlux)** | **MSAR ≥ 168 req/s — CONTROL-CENSORED.** GREEN 3/3 at the same ceiling. |

**M2 vs M3 exact ceiling ranking: INCONCLUSIVE** — both are censored by the single-host test rig,
not separated by the data. 168 req/s is the rig's control-safe maximum, **not** an architecture
limit.

### Resource footprint at equal sustained load (Open R = 168, both GREEN)

| | M2 (Virtual Thread) | M3 (WebFlux) |
|---|---|---|
| JVM platform threads (peak) | ~110 | ~30 |
| CPU (avg cores of 10) | 0.39 | 0.33 |
| RSS peak | ~1159 MiB | ~450 MiB |
| throughput / TTFC p95 / duration p95 | 168/s / 1.03 s / 8.07 s | 168/s / 1.02 s / 8.06 s |

Same throughput and latency; M3's platform-thread and memory footprint is markedly smaller **at
this rate / workload / runtime**. This is a bounded observation, not a general efficiency ranking —
neither model's ceiling is known, and Phases 2–3 already showed platform-thread savings do not
translate into CPU/RSS savings.

---

## D. Technical findings

- **Capacity ≠ service-quality boundary.** M1 breaks SLO while its queue is barely used: at N = 56
  the queue holds ~6 items, nothing is rejected, every request completes — but TTFC p95 has already
  jumped 1.0 s → 8.9 s. The boundary is **queue-wait latency**, and hard rejection/timeout is a
  separate, later boundary (`executor_rejected_final = 0` even at N = 400 with queue depth 350).
- **Virtual threads: sub-linear thread growth.** M2's JVM platform-thread count grows ×1.22 for a
  ×1.75 rise in concurrency (N=640 → 228, N=1120 → 279). Not 1:1.
- **WebFlux: flat thread footprint.** M3 holds 30 platform threads at 640, 1120, and ~1370
  concurrent streams alike — independent of concurrency.
- **A load-generator bug that looked like a server limit.** xk6-sse v0.1.11 creates a new
  `http.Transport` per `sse.open()` and never closes it; each completed iteration leaked an idle
  connection for the server to reclaim, inflating the measured connection population ~8×. Fixed
  with an explicit `client.close()` → M2 R=160 organic connection count dropped ~10 549 → ~1 276
  (Little's Law: 160 × 8 s ≈ 1280).
- **A shared-host confound that looked like a Virtual-Thread ceiling.** On one machine the load
  generator (k6→gateway) and the gateway (gateway→Mock) draw from the *same* 16 384-port OS
  ephemeral-port pool. Two-leg endpoint-aware socket telemetry showed their port demands are
  disjoint (A∩B ≈ 1) and sum to **A∪B ≈ 16 362** at R = 256 → `EADDRNOTAVAIL` on both legs, while
  every JVM/CPU/connector resource had headroom. Recalibrated to a control-safe ceiling of
  **168 req/s** (`A∪B` ≤ 80 % of the pool — the reciprocal of the project's frozen 1.25× headroom
  factor, fixed *before* the calibration ran).

---

## E. What I learned

- **Measure the wall, don't assume it.** Two "Virtual-Thread failures" turned out to be a Tomcat
  connector default and a shared-host OS port pool. Separating a model limit from a rig limit was
  most of the work, and is the part worth keeping.
- **A finding you can't explain is not a result.** Every anomaly (connector cap, port exhaustion,
  loadgen transport leak, a clock discontinuity, a mis-timed Canary gate check) was investigated
  and disclosed, and no raw run was deleted or re-run to get a nicer number.
- **"Better concurrency model" is the wrong question.** At equal admitted load the three models are
  latency/throughput-equivalent. They differ in where they stop scaling, how observable that
  boundary is, and what they cost per unit of headroom — and the right choice is a checklist
  (blocking I/O? expected concurrency? bounded-overload policy? memory / thread / FD budget?
  programming-model complexity?), not a benchmark headline.

---

## F. Résumé line

> Measured the concurrency-scalability boundaries of Platform-Thread+Queue, Virtual-Thread, and
> WebFlux gateways on one fixed Java 21 runtime across two axes (concurrent-stream capacity and
> sustained arrival rate); found a clear Formal-confirmed boundary for the thread-pool model
> (MSC [50,56], MRC [350,400], MSAR [6,7] req/s) and control-censored lower bounds for the other
> two (≥ 1120 concurrent streams, ≥ 168 req/s), after isolating a shared-host OS ephemeral-port
> confound that had masqueraded as a Virtual-Thread ceiling.

Shorter:

> Benchmarked thread-pool vs virtual-thread vs WebFlux scalability boundaries on a fixed Java 21
> runtime; separated a genuine thread-pool boundary from a test-rig OS-resource limit, and reported
> the latter two models as control-censored rather than overclaiming a maximum.

---

## G. Portfolio blurb (3–5 lines)

Phase 4 of a personal concurrency-lab: I compared three server concurrency architectures
(platform-thread pool + bounded queue, virtual-thread-per-request, and Spring WebFlux) on one fixed
Java 21 / Spring Boot runtime and streaming workload, measuring *where each stops being
sustainable* rather than raw throughput. The thread-pool model has a clean, Formal-confirmed
boundary on both a "how many streams held at once" axis (MSC [50, 56]) and a "what arrival rate
sustained" axis (MSAR [6, 7] req/s); its SLO breaks via queue-wait latency well before anything is
rejected. Virtual-thread and WebFlux never hit a boundary inside the test rig's control-valid range
(≥ 1120 concurrent streams, ≥ 168 req/s) — and proving that meant isolating a shared-host OS
ephemeral-port-pool confound that had looked like a Virtual-Thread scheduler ceiling. The exact
Virtual-Thread-vs-WebFlux ranking is reported as INCONCLUSIVE, not fudged into a winner.
