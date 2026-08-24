# gateway-mvc-webclient (P3-B)

Phase 3 Unit 3 — the non-blocking-outbound Servlet control. Java 8 + Spring Boot 2.7.18 + Spring
MVC (Tomcat, embedded) + WebClient (Reactor Netty 1.0.39). This is the second point on Experiment
A's comparison line (`docs/test-plan/phase3-design.md`): same Servlet write path as P3-A
(`gateway-mvc-blocking-spring5`), only the outbound client differs.

**Not end-to-end non-blocking.** This is still a Servlet application — `AsyncContext` +
`PrintWriter` + a dedicated bounded Servlet write executor are all still here, unchanged in
semantics from P3-A. Only the *outbound* call to Mock LLM is non-blocking. Full reactive
end-to-end (no Servlet layer at all) is P3-C (Unit 4).

**Scope note:** as with P3-A, pool sizes / buffer capacities / connection-pool limits below are
**functional defaults, not Formal-frozen values**.

## Why MVC + WebClient (not WebFlux server)

Unit 0 already confirmed by direct measurement (`docs/decisions/phase3-version-compatibility.md`
§4-2) that having both `spring-boot-starter-web` and `spring-boot-starter-webflux` on the classpath
still makes Spring Boot choose `WebApplicationType.SERVLET` (Tomcat) — WebFlux is present here
purely to bring in `WebClient`/Reactor Netty as a client library. This module's own startup log
confirms the same: `Tomcat started on port(s): 8082`, no embedded Netty *server* startup line.

## Architecture

```
Front -> Spring MVC Controller -> Servlet AsyncContext -> admission tryAcquire()
  -> WebClient POST /mock/stream -> Reactor Netty SSE receive -> fast non-blocking onNext
  -> PerStreamWriteChannel.offer(frame) -> shared bounded Servlet write executor
  -> PrintWriter.write + flush -> Front
```

There is no blocking-outbound Platform Thread pool here — WebClient's call runs on Reactor Netty's
own event loop. The absolute deadline is enforced by two mechanisms sharing the same
`deadlineNanos` (`docs/decisions/phase3-timeout-cancellation.md` §9/§10): a shared
`ScheduledExecutorService` watchdog (the same authoritative mechanism P3-A uses) and a supplementary
Reactor operator (`AbsoluteDeadline`, Unit 1's verified candidate C) that lets the WebClient
subscription self-cancel at the same instant instead of relying solely on an externally-triggered
`dispose()`.

## Running

```bash
JAVA_HOME=<path to Zulu 8 home> ./gradlew bootJar
MOCK_LLM_BASE_URL=http://localhost:8000 JAVA_HOME=<...> \
  java -jar build/libs/gateway-mvc-webclient-0.1.0.jar
```

Mock LLM must already be running.

## Endpoints

Same as P3-A: `POST /chat/stream`, `GET /healthz`, `GET /actuator/health`,
`GET /actuator/prometheus`.

## Config (env vars)

Same names as P3-A for the shared concerns (`CHAT_ADMISSION_LIMIT`, `CHAT_TOTAL_TIMEOUT_MS`,
`CHAT_ASYNC_WATCHDOG_MARGIN_MS`, `SERVLET_WRITE_POOL_SIZE`, `SERVLET_WRITE_QUEUE_CAPACITY`,
`SERVLET_PER_STREAM_BUFFER_CAPACITY`, `SERVER_PORT`, `MOCK_LLM_BASE_URL`, `LOG_LEVEL`), plus (Unit
3 §12, `docs/decisions/phase3-admission-connection-pool.md` §3):

| Var | Functional default | Meaning |
|---|---|---|
| `WEBCLIENT_MAX_CONNECTIONS` | = `CHAT_ADMISSION_LIMIT` | ConnectionProvider pool size — kept ≥ admission ceiling so pending acquisition is structurally unreachable in normal operation |
| `WEBCLIENT_PENDING_ACQUIRE_MAX_COUNT` | 1 | Safety net only, should never fire (Reactor Netty 1.0.39 does not support `0` — confirmed by direct execution in Unit 1, throws `IllegalArgumentException`) |
| `WEBCLIENT_PENDING_ACQUIRE_TIMEOUT_MS` | 1000 | Safety net only |
| `WEBCLIENT_CONNECT_TIMEOUT_MS` | 3000 | Low-level connect timeout |
| `WEBCLIENT_RESPONSE_TIMEOUT_MS` | 120000 | Low-level Reactor Netty response timeout — a safety net, NOT the absolute deadline (`CHAT_TOTAL_TIMEOUT_MS` is authoritative; Unit 3 §14 explicitly forbids conflating the two) |

No `CHAT_BLOCKING_POOL_SIZE` — there is no blocking outbound executor here.

## SSE decoding (Unit 3 §5)

WebClient decodes into `ServerSentEvent<String>` via Spring's own official SSE codec (not raw
`DataBuffer` parsing). `SseFrameFormatter` then reconstructs the exact wire frame P3-A relays
byte-for-byte (`"event: <name>\ndata: <payload>\n\n"`) from the decoded fields. This is safe
because Mock LLM's `sse_event()` (`mock-llm-fastapi/app/main.py`) always emits exactly that 2-field
shape for every event — see `SseFrameFormatter`'s javadoc for the full reasoning, including why
this satisfies Unit 1's client-observable contract even though it isn't literal byte-passthrough.

## Metrics

Same common (`gateway.*`) and shared Servlet-write (`servlet.write.*`) metrics as P3-A, plus
Reactor Netty's own `reactor_netty_connection_provider_*`/`reactor_netty_http_client_*` (confirmed
real names in `docs/test-results/phase3/unit3-p3b-functional/`). **No `outbound.blocking.*`
metrics** — there is no such executor here, and `GatewayMetrics` deliberately omits those entries
rather than exposing them as always-zero placeholders (Unit 3 §22).

## Functional smoke verification

Full results: `docs/test-results/phase3/unit3-p3b-functional/SUMMARY.md`. Highlights:

- **Smoke A**: found and fixed a real bug — `PerStreamWriteChannel.markProducerDone()`'s fast path
  (buffer already empty when upstream completes) was running `AsyncContext.complete()` inline on a
  `reactor-http-nio-*` thread, crossing the "Reactor callback never does Servlet-side work"
  boundary. Fixed by dispatching that path through the write executor. PASS after the fix.
- **Smoke B**: the headline functional finding of this Unit. Using the identical stall scenario as
  P3-A's Unit 2 Smoke B, the deadline watchdog fired at the same relative time (+2507ms vs P3-A's
  +2505ms), but here the cancellation reached Mock LLM in the *same millisecond* — no ~1.1s lag.
  Direct, measured confirmation that P3-B does not have P3-A's "blocking worker survives past the
  deadline" limitation, because there's no blocking read to fail to interrupt.
- **Smoke C**: admission rejects in 4.6ms; `reactor_netty_connection_provider_pending_connections`
  stayed 0 throughout, confirming the pool never became a hidden second limiter.
  Smoke D: exactly-once terminal outcome under a real disconnect race, same as P3-A.

## Tests

`./gradlew test` — 32 unit tests. Ported verbatim from P3-A where the code is verbatim
(`PerStreamWriteChannelTest`, `AdmissionGateTest`), adapted where the lifecycle resource type
changed (`RequestLifecycleTest`: `Disposable` mocks instead of `HttpURLConnection`/`Future`), plus
new tests specific to this module: `SseFrameFormatterTest`, `AbsoluteDeadlineTest` (the four
lifecycle cases from Unit 1's spike, now permanent regression tests against the shipped operator),
and `DeadlineVsWriteDrainRaceTest` — the Unit 3 §24 "very important" case: upstream signals
completion well before the deadline, but the write executor is saturated/slow, so the buffered
final frame hasn't actually been written yet; the deadline watchdog must still win and report
`timeout`, not `completed`.

A static grep check (`grep -rnE ".block(|.blockFirst(|.blockLast(|Future.get(|toFuture().get(|Thread.sleep(" src/main/java/`) confirms none of the forbidden blocking calls appear in main source.

## P3-A / P3-B Structural Comparison

Basis for Experiment A's variable control (`docs/test-plan/phase3-design.md` §1).

**Identical** (byte-for-byte, `diff` confirms): `AdmissionGate.java`, `PerStreamWriteChannel.java`
minus the one line noted below, `EnvUtil.java`, `GatewayApplication.java`, `HealthController.java`.
Identical in *shape and semantics* (same class/method names, same exactly-once CAS logic, same
setter-closes-the-race pattern): `RequestLifecycle.java`, `ChatController.java`'s
admission/rejection/AsyncListener wiring, `GatewayMetrics.java`'s common-metric portion.

**Different, by design (Experiment A's controlled variable):**

| | P3-A | P3-B |
|---|---|---|
| Outbound client | `java.net.HttpURLConnection` | `WebClient` (Reactor Netty) |
| Outbound execution | Dedicated blocking Platform Thread pool (`ThreadPoolExecutor`, core=max=admission ceiling, `SynchronousQueue`+`AbortPolicy`) | None — runs on Reactor Netty's own event loop |
| Upstream cancellation | `HttpURLConnection.disconnect()` + `Future.cancel(true)` — **measured not to promptly interrupt an in-flight blocked read** (Unit 2 Smoke B) | `Subscription.cancel()` via `Disposable` — **measured to cancel within the same millisecond as the deadline** (Unit 3 Smoke B) |
| SSE input decoding | Manual `SseFrameReader` (raw `BufferedReader.readLine()` framing) | Spring's official `ServerSentEvent<String>` codec + `SseFrameFormatter` reconstruction |
| `RequestLifecycle` resource fields | `HttpURLConnection connection`, `Future<?> outboundFuture` | `Disposable upstreamSubscription` |
| `ExecutorConfig` beans | `blockingOutboundExecutor`, `servletWriteExecutor`, `deadlineWatchdogExecutor` | `servletWriteExecutor`, `deadlineWatchdogExecutor` |
| `GatewayMetrics` | includes `outbound.blocking.*` | omits it entirely (no placeholder) |
| Absolute deadline enforcement | remaining-budget clamp (once, at connect) + per-line recheck + watchdog | shared watchdog (authoritative) + `AbsoluteDeadline` Reactor operator (supplementary, same `deadlineNanos`) |
| `PerStreamWriteChannel.markProducerDone()` | calls `tryTerminate()` inline when buffer already empty | dispatches that same call through the write executor (Unit 3 Smoke A finding — avoids running `AsyncContext.complete()` on a Reactor thread) |

**Same class, one-line functional divergence:** `PerStreamWriteChannel.java` — see
`docs/test-results/phase3/unit3-p3b-functional/diff-PerStreamWriteChannel.txt` for the exact diff
and the finding that motivated it.
