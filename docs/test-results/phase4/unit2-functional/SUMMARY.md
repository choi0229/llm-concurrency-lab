# Phase 4 Unit 2 — Functional Validation Summary

Scope: M1/M2/M3 implementation + functional parity only. No scalability load, no concurrency
sweep, no Direct Mock calibration, no Formal Benchmark. All requests below were single/small
functional smokes (max 3 concurrent, in the F5/F6 tests).

## Modules created

- `gateway-phase4-platform-queue` (M1) — port 18101
- `gateway-phase4-virtual-thread` (M2) — port 18102
- `gateway-phase4-webflux` (M3) — port 18103

All three: `./gradlew clean build` → BUILD SUCCESSFUL (compile + test + jar), native arm64
Temurin 21.0.11+10, Gradle 8.14, Spring Boot 4.1.0 (Unit 0 runtime, unchanged).

## Functional scenario results (all against the real Mock LLM, `mock-llm-fastapi`, unmodified)

| Scenario | M1 | M2 | M3 |
|---|---|---|---|
| F1 Normal SSE | PASS — HTTP 200, delta×3+final, `completed`×1, postflight clean | PASS — identical, `isVirtual=true` confirmed via `/diag/virtual-thread-check` | PASS — HTTP 200, same event/data/order/final (raw formatting differs, expected), postflight clean |
| F2 Absolute timeout (`CHAT_TOTAL_TIMEOUT_MS=2500`, slow stream) | PASS — cut at ~2.7s, `timeout`×1, postflight clean | PASS — same | PASS — same, deadline operator (Candidate C pattern) confirmed working |
| F3 Client disconnect (partial read via `head -c`) | PASS — `client_disconnect`×1, postflight clean | PASS — same | PASS — same (native WebFlux CANCEL propagation, no explicit code needed) |
| F4 Upstream connection failure (unused port) | PASS — HTTP 200 empty body (early-committed Servlet response, expected per Phase 3 precedent), `upstream_error`×1 | PASS — same | PASS — **HTTP 500** (lazy-commit WebFlux response, expected architectural difference per `docs/decisions/phase3-metrics-contract.md` section 8-3), `upstream_error`×1 |
| F5 Executor rejection (test-only worker=1/queue=1) | PASS — 3rd request → HTTP 503, `executor_rejected_total=1`, `outcome=rejected`×1 | n/a (M2 never rejects) | n/a |
| F6 Queued-task timeout, no phantom upstream | PASS — deterministic JUnit test (`PlatformTaskSubmitterTest`); HTTP-level timing test attempted first and found **structurally impossible** to force reliably (see note below) | n/a | n/a |
| F7 Final-drain (final frame not lost) | PASS — `WriteChannelTest` (shared M1/M2 code) | PASS — same test, copied verbatim | n/a (no write queue) |
| F8 Write overflow | PASS — `WriteChannelTest` | PASS — same | n/a |
| F9 Virtual-thread evidence | n/a | PASS — both live `/diag/virtual-thread-check` (`isVirtual=true`) and `VirtualTaskSubmitterTest` (metric cleanup + isVirtual) | n/a |
| F10 No-blocking-architecture static audit | n/a | n/a | PASS — zero forbidden constructs in application source, zero Tomcat/Servlet in jar |

**F6 note (important, not a bug)**: with a single shared `CHAT_TOTAL_TIMEOUT_MS` applied
identically from each request's own admission time, a request admitted earlier always reaches its
own deadline no later than a request admitted after it. This makes it **mathematically impossible**
to reliably force "queued request's deadline fires while an earlier, unrelated request still
legitimately occupies the worker" purely via HTTP-level timing — the earlier request's own deadline
always arrives first (or during the same instant), freeing the worker at or before the later
request's deadline. Two curl-based attempts confirmed this: both times, request #2 got dispatched
just before its deadline and completed successfully. The correct test is a deterministic unit test
that decouples worker-occupation duration from the deadline constant (`CountDownLatch`-controlled
blocking task, unrelated to any `RequestLifecycle`) — implemented as
`PlatformTaskSubmitterTest.queuedTaskThatTimesOutNeverRunsAndIsRemovedFromQueue`, which proves:
zero upstream calls for the timed-out task, task removed from the executor queue, and correct
`TIMEOUT` outcome — deterministically, every run.

## Unit/integration tests

- M1: 4 tests (`PlatformTaskSubmitterTest`×2, `WriteChannelTest`×2) — all PASS.
- M2: 3 tests (`VirtualTaskSubmitterTest`×1, `WriteChannelTest`×2, copied verbatim from M1) — all
  PASS.
- M3: 0 JUnit tests (F1-F4/F10 covered by live functional runs + static grep audit instead —
  `./gradlew test` reports `NO-SOURCE`/clean, not a failure).

Full per-suite counts: `test-results.txt`.

## Correctness bugs found and fixed during this Unit

1. **Reactor Netty metrics not exposed by default** — `reactor_netty_connection_provider_*` and
   `reactor_netty_http_server_*` did not appear on `/actuator/prometheus` even with Actuator +
   Micrometer on the classpath. Root cause: Reactor Netty requires explicit `.metrics(true, ...)`
   on both the client (`ConnectionProvider`/`HttpClient`) and the server (via a
   `NettyServerCustomizer` bean) — matches the Phase 3 precedent
   (`docs/decisions/phase3-metrics-contract.md` section 2-3) under a different Reactor Netty
   version. Fixed in `WebClientConfig` and the new `NettyServerMetricsConfig`.
2. **`org.springframework.boot.web.embedded.netty.NettyServerCustomizer` no longer exists** in
   Boot 4.1.0 — Boot 4.x modularized the Netty web-server support into a separate
   `spring-boot-reactor-netty` artifact; the class moved to
   `org.springframework.boot.reactor.netty.NettyServerCustomizer`. Found and fixed via inspecting
   the actual resolved jars rather than guessing from memory of older Boot generations.
3. **Type-inference compile error** in `VirtualThreadDiagnosticController` (`Map.of` with mixed
   `String`/`boolean` values inferring an intersection type) — fixed with an explicit
   `Map<String,Object>`.
4. Flawed initial F6 test design (HTTP-timing-based) — identified as structurally unreliable (see
   above) before it produced a false pass/fail; replaced with a deterministic unit test.

No correctness bugs were found in the core lifecycle/outcome/write-path logic itself — F1-F5,
F7-F10 all passed on the first implementation.

## Postflight

Confirmed clean (`gateway_active_requests=0`, `gateway_upstream_active=0`, and all M1/M2-specific
gauges =0) after every functional scenario, every model, every run this Unit. Raw evidence:
`m{1,2,3}/prometheus.txt` (final state, one F1 request run against each for metric population).

## Scalability load executed this Unit

**Zero.** Maximum concurrent in-flight requests during any test this Unit: 3 (F5). No N-sweep, no
RPS-sweep, no Direct Mock calibration.
