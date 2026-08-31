# LLM Concurrency Lab — Portfolio Summary (Phase 1 → 4)

Every claim here is proven in
[`docs/test-results/llm-concurrency-lab-final-report.md`](../test-results/llm-concurrency-lab-final-report.md)
and the four phase Final Reports. No claim is added here that those do not establish.

---

## A. One-sentence description

A four-phase benchmark study of how three JVM server concurrency models — platform-thread pool +
bounded queue, virtual-thread-per-request, and Spring WebFlux — behave and where they stop being
sustainable when they relay an LLM's SSE stream under load.

---

## B. The problem

A real production shape: `Front → Spring MVC → AsyncContext → ThreadPoolExecutor →
HttpURLConnection → LLM SSE → relay`. Under load it degrades, and the usual advice ("use virtual
threads", "go reactive") is backed by throughput graphs, not by *where the cost goes* or *how far
each model actually carries it*. This lab measures that.

---

## C. Experiment progression (one variable per phase, different runtime per phase — by design)

| phase | isolated variable | headline finding |
|---|---|---|
| **1** — Java 8 | `ThreadPoolExecutor` config (core/max/queue/rejection) | Overload cost doesn't vanish — it moves to queue wait / 503 reject / platform-thread growth / caller-thread propagation. **Queue wait breaks client latency first** (~9.6× TTFC in a two-config natural experiment). |
| **2** — Java 21 | chat executor: platform pool → virtual-thread-per-task | Platform-thread count **decoupled from load** (peak −30 % to −52 %, flat as load rises) — but **RSS/CPU don't follow** (CPU +18–27 %). Unlimited admission just relocates the backlog. Admission control still needed. No observable virtual-thread pinning. |
| **3** — Java 8 | outbound client (blocking → WebClient), then server model (Servlet → WebFlux) | Platform-thread peak **80 → 49 → 37** (−53.75 %) with no throughput/latency change — **CPU/RSS again don't follow** (A→B CPU +27–32 %). Phase 2's pattern reproduces at the server-model layer. |
| **4** — Java 21 | the *scalability boundary* of each model (Closed + Open) | Platform-thread+queue has a found boundary; virtual-thread and WebFlux don't, within the single-host control-valid range — after isolating a shared-host OS ephemeral-port confound. |

---

## D. Core architecture comparison

| | M1 — Platform pool + bounded queue | M2 — Virtual thread per request | M3 — WebFlux + WebClient |
|---|---|---|---|
| model | blocking, imperative | blocking, imperative | reactive |
| platform threads vs concurrency | grow with load | **sub-linear** | **flat (~30)** |
| first failure signature | queue-wait SLO break → deadline timeout / rejection | none reached (rig's OS port pool binds first) | none reached (rig's OS port pool binds first) |
| overload behaviour | explicit, bounded, back-pressured | needs an added admission gate | pooled outbound `ConnectionProvider` |
| operational complexity | lowest | low (blocking code kept) | highest (reactive debugging) |

---

## E. Key numbers

**Boundary table** (M1 Formal-confirmed; M2/M3 lower bounds, CONTROL-CENSORED):

| Architecture | Closed MSC | Closed MRC | Open MSAR |
|---|---|---|---|
| M1 (Platform pool + queue) | **[50, 56]** | **[350, 400]** | **[6, 7] req/s** |
| M2 (Virtual thread) | **≥ 1120** censored | **≥ 1120** censored | **≥ 168 req/s** censored |
| M3 (WebFlux) | **≥ 1120** censored | **≥ 1120** censored | **≥ 168 req/s** censored |

**M2 vs M3 exact ceiling ranking: INCONCLUSIVE.**

**Same-load resource footprint (Open R = 168, both GREEN, median of 3):**

| | M2 (Virtual Thread) | M3 (WebFlux) |
|---|---|---|
| platform threads (peak) | ~110 | ~30 |
| RSS peak | ~1159 MiB | ~450 MiB |
| CPU (cores of 10) | 0.39 | 0.33 |
| throughput / TTFC p95 / duration p95 | 168/s / 1.03 s / 8.07 s | 168/s / 1.02 s / 8.06 s |

Same throughput and latency; markedly different footprint — a bounded observation at this
rate/workload/runtime, **not** a general efficiency ranking (neither ceiling is known).

**Cross-phase thread trend** (direction only, not absolute — different runtimes): platform-thread
peak fell −30–52 % (Phase 2, virtual thread) and 80 → 37 (Phase 3, blocking → WebFlux), with **no**
matching CPU/RSS reduction in either phase.

---

## F. Measurement-integrity work (the part worth showing)

- **`ThreadPoolExecutor` is `core → queue → max`**, not `core → max → queue` — reproduced and used
  as a design constraint.
- **JFR pinning detector verified on a positive control** (5/5) before concluding "0 events on the
  real Gateway".
- **A wall-clock discontinuity** caught by a wall-vs-monotonic cross-check → one invalid rep
  replaced (not retried for a nicer number).
- **A "virtual-thread ceiling" that was three test-rig artifacts:** Tomcat's default
  `maxConnections = 8192` → then `EADDRNOTAVAIL` → then xk6-sse leaking a transport connection per
  iteration (fixed: connection population 10.5k → 1.28k) → then the load generator and the gateway
  **sharing one 16 384-port OS ephemeral pool on one host** (two-leg telemetry: A∪B ≈ 16 362,
  A∩B ≈ 1). Recalibrated the safe rate 256 → 168 with a headroom rule frozen *before* the
  calibration ran.
- **Socket sampler double-counted loopback connections** (client + server-accepted rows) — fixed
  to endpoint-aware.

Every anomaly was root-caused and disclosed; no raw run was deleted or re-run to change a result.

---

## G. Practical decision guide

- **Need a hard, understood, back-pressured ceiling?** Platform pool + bounded queue — but size
  the pool/queue to the SLO (queue wait breaks latency while the queue is barely used), not to
  "not rejecting".
- **Have a large blocking-I/O codebase and need more concurrency without a rewrite?** Virtual
  threads — but keep admission control, timeouts, fast-fail, monitoring; RSS/CPU won't fall with
  the thread count.
- **Platform-thread- or memory-budget constrained, team can carry reactive?** WebFlux — flat
  ~30-thread footprint, ~⅓ the RSS of the virtual-thread model at the one equal-load point
  measured; pay for it in reactive complexity and observability.
- **No "always X".** At equal admitted load, all three were latency/throughput-equivalent in every
  phase.

---

## H. Résumé line

> Benchmarked platform-thread-pool vs virtual-thread vs WebFlux LLM-SSE gateway scalability on a
> fixed Java 21 runtime across two axes (concurrent-stream capacity and sustained arrival rate);
> Formal-confirmed the thread-pool model's boundary (MSC [50,56], MRC [350,400], MSAR [6,7] req/s)
> and reported the other two as control-censored lower bounds (≥ 1120 streams, ≥ 168 req/s) after
> isolating a shared-host OS ephemeral-port confound that had masqueraded as a virtual-thread
> ceiling.

---

## I. One-minute interview version

"I had a Spring Gateway relaying an LLM's SSE stream behind an async servlet, a thread pool, and a
blocking HTTP client, and it degraded under load. Phase 1 showed the async servlet only frees the
Tomcat thread, not the relay worker — and that *queue wait*, not rejection, is what breaks client
latency first; I measured a 9.6× TTFC gap between two executor configs that differ only in whether
a queue exists. Phases 2 and 3 tried removing thread-per-request — virtual threads, then reactive —
and both cut the platform-thread count a lot (−52 %, then 80→37), but neither cut CPU or memory.
Phase 4 asked where each model's real boundary is: the thread-pool model has a clean, confirmed one
on both a 'streams held' axis and an 'arrival rate sustained' axis; virtual threads and WebFlux
never hit a boundary inside the test rig's valid range. Proving that meant debugging what looked
like a virtual-thread ceiling down through a Tomcat connector default, a load-generator connection
leak, and finally the load generator and the gateway sharing one OS ephemeral-port pool on one
host. So I reported those two as control-censored lower bounds and the exact ranking between them as
inconclusive, rather than picking a winner the data didn't support."

---

## J. Deeper interview questions this project answers

- *Why is `ThreadPoolExecutor` `core → queue → max`, and how did you use that?*
- *What exactly does an async servlet free, and what stays occupied during a blocking SSE relay?*
- *You cut platform threads 50 %+ twice — why didn't CPU or RSS follow?*
- *What is `MODEL_TIMEOUT` vs `MODEL_REJECTION`, and why does an SLO break before either?*
- *How did you tell a virtual-thread limit apart from a Tomcat connector limit apart from a load-
  generator bug apart from an OS ephemeral-port limit?*
- *What does "CONTROL-CENSORED" mean and why is it not "the maximum"?*
- *You froze an 80 % ephemeral-port headroom rule — where did 80 % come from, and why before the
  run?*
- *M2 and M3 were both GREEN at 168 req/s with identical latency — why is the ranking still
  INCONCLUSIVE?*
- *What would Phase 4.2 (separate load-generator host) actually change?*
