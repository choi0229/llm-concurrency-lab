# gateway-webflux (P3-C)

Phase 3 Unit 4 — the end-to-end reactive control. Java 8 + Spring Boot 2.7.18 + Spring WebFlux
(Reactor Netty server) + WebClient (Reactor Netty client). This removes everything P3-B
(`gateway-mvc-webclient`) still carried over from the Servlet model — `AsyncContext`,
`PrintWriter`, the Servlet write executor, `PerStreamWriteChannel` — none of it exists here. That
absence is Experiment B's result, not an implementation shortcut.

**"End-to-end reactive" here means exactly this and no more:** admission, the outbound call, and
the response body are one continuous Reactor chain with no separate buffering/execution layer
decoupling "upstream received a frame" from "frame reached the client." It does **not** mean
"non-blocking = automatically faster" — this module makes no performance claim (Unit 4 §37/§39
forbid it), and none of its functional smoke evidence should be read as one.

## Architecture

```
Front -> Reactor Netty Server -> WebFlux Handler -> non-blocking admission
  -> WebClient -> Mock LLM SSE Flux -> SSE response Flux -> Reactor Netty Server -> Front
```

No dependency on `spring-boot-starter-web`/Tomcat, no `javax.servlet`/`jakarta.servlet` import
anywhere in main source (grep-verified). Startup log confirms `NettyWebServer`, never Tomcat.

### The three-tier picture (Unit 4 §37)

```
P3-A:  blocking outbound Platform Thread workers  +  Servlet write executor threads
P3-B:  WebClient/Reactor Netty event loops          +  Servlet write executor threads
P3-C:  Reactor Netty event loops (server + client)  +  (nothing else — no second execution layer)
```

No performance ranking implied by this picture — it's a resource-topology map, not a benchmark
result.

## Why `RouterFunction`, not `@RestController` (found by testing, not by design)

Two earlier attempts both failed Smoke A before this shipped:

1. `@RestController` returning `Mono<ServerResponse>` — `@RestController` implies
   `@ResponseBody`, which tried to Jackson-serialize the `ServerResponse` object itself instead of
   treating it as an already-built response (`CodecException: No serializer found for ...
   DefaultEntityResponse`, connection aborted after the 200 already committed).
2. Plain `@Controller` returning `Mono<ServerResponse>` — fell through to Spring's default
   view-name resolution instead (`IllegalStateException: Could not resolve view with name
   'chat/stream'`).

`RouterFunction`/`HandlerFunction` (`@Bean RouterFunction<ServerResponse> chatRoutes()` wiring a
plain `stream(ServerRequest)` method) is `ServerResponse`'s actual native use case and worked
immediately. Full account in `ChatController`'s javadoc — kept there deliberately so nobody
re-attempts the annotated-controller approach without knowing why it failed twice.

## Admission (Unit 4 §6/§7)

Same non-blocking `Semaphore.tryAcquire()` semantics as P3-A/B, same `CHAT_ADMISSION_LIMIT`
default (50). Resolved synchronously in the handler, **before** any `Mono`/`Flux` for the request
is built — a reject can never race a 200 response getting committed first. Confirmed: second
request under `CHAT_ADMISSION_LIMIT=1` rejected in 6.7ms with the same `503` +
`{"status":"REJECTED","reason":"executor_saturated"}` body P3-A/B use.

## WebClient / connection pool / `LoopResources` (Unit 4-0)

Identical `WebClientConfig` to P3-B, including the explicit dedicated `LoopResources`
(`gateway-webclient-loop`) added in Unit 4-0 specifically so P3-B and P3-C's outbound client
topology stays symmetric — see `docs/decisions/phase3-reactor-resource-topology.md`. Confirmed by
`jstack` on a running P3-C instance: 10× `gateway-webclient-loop-nio-N` (client) with **zero name
overlap** against 5× `reactor-http-nio-N` (the WebFlux server's own, separate, default event loop).

## Absolute deadline (Unit 4 §13/§14)

The same `AbsoluteDeadline` operator verified in Unit 1 and used unmodified from P3-B, applied
directly to the whole upstream+relay chain — no separate `ScheduledExecutorService` watchdog
thread, unlike P3-A/B. This is a deliberate simplification, not an oversight (Unit 4 §14): P3-A/B
needed an external watchdog specifically because their Servlet write path is decoupled from the
upstream subscription; P3-C's single continuous chain means wrapping the outer Flux is sufficient
— cancelling it cancels everything transitively upstream of it, and there's no separate resource
downstream of it left uncancelled.

## Cancellation / outcome classification (Unit 4 §11/§12)

`ChatController.attachLifecycle()` wires `doOnComplete`/`doOnError`/`doOnCancel` directly — never a
bare `doFinally(SignalType)` guessing the cause after the fact:

| Reactor signal | Outcome | Why this is the only explanation reachable here |
|---|---|---|
| `onComplete` | `completed` | Normal upstream EOF, all items already handed downstream |
| `onError` (`AbsoluteDeadlineExceededException`) | `timeout` | The deadline operator always surfaces as an error, never a raw cancel (Unit 1 ADR) |
| `onError` (anything else) | `upstream_error` | Real WebClient/Mock LLM failure |
| `onCancel` | `client_disconnect` | Admission rejection never reaches this Flux (resolved before it's built); the deadline surfaces as `onError`, not cancel — so a cancel reaching here has exactly one remaining cause: the Front connection going away |

Verified directly against this wiring (not just against the isolated `AbsoluteDeadline` operator) in
`ChatControllerLifecycleWiringTest`, including deadline-vs-upstream-error and
deadline-vs-downstream-cancel races (10 repeated iterations each, asserting exactly one of the two
plausible outcomes and that `gateway.upstream.active` always returns to zero).

## `RequestLifecycle` — deliberately not a port of P3-A/B's

No `AsyncContext`, no `Disposable`/`Future`/`HttpURLConnection` field, no deadline-task
`ScheduledFuture` to cancel. Only what Unit 4 §9 asks for: the terminal CAS and exactly-once
permit/metric bookkeeping. There's nothing else to own because there's no decoupled resource left
to race-close against — see the class javadoc.

## Metrics

Common `gateway.*` only — **no** `servlet.write.*` (no such executor/buffer exists) and **no**
`outbound.blocking.*` (no such executor exists), matching P3-B's already-established "don't fake
presence of a missing resource" policy, now extended one step further. Reactor Netty client-side
metrics (`reactor_netty_connection_provider_*`, `reactor_netty_http_client_*`) are present via the
same `WebClientConfig` as P3-B. **`reactor_netty_http_server_*` metrics do not exist** in this
setup — confirmed by direct `/actuator/prometheus` inspection, not assumed (Boot's default embedded
server doesn't enable Reactor Netty server metrics on its own).

## Running

```bash
JAVA_HOME=<path to Zulu 8 home> ./gradlew bootJar
MOCK_LLM_BASE_URL=http://127.0.0.1:8000 JAVA_HOME=<...> \
  java -jar build/libs/gateway-webflux-0.1.0.jar
```

`127.0.0.1` (not a hostname) per this Unit's §18 DNS policy — removes host-resolution as a variable
across all three implementations; also sidesteps the (functionally harmless) `Unable to load
MacOSDnsServerAddressStreamProvider` warning every P3-B/P3-C run has logged since Unit 3
(root-caused, not fixed, in `docs/decisions/phase3-reactor-resource-topology.md` §6 — a missing
native-classifier jar for this platform, irrelevant once DNS resolution itself isn't needed).

## Functional smoke verification

Full results: `docs/test-results/phase3/unit4-p3c-functional/SUMMARY.md`. Highlights:

- **Smoke A**: a handler-model/API integration mismatch (annotated controller vs. `ServerResponse`,
  not a claimed framework defect — see above) found and corrected before it could pass at all. Once
  corrected: all 6 SSE events in order, no final-event-loss bug (there's no separate buffer for one
  to hide in). PASS.
- **Smoke B**: same stall scenario as P3-A/B. Client-observed total time 2.524s against a 2.500s
  deadline — cancellation reached Mock LLM at the same instant as P3-B (prompt), in contrast to
  P3-A's measured ~1.1s blocking-read lag (Unit 2). PASS, with a noted log-noise characteristic
  (the deadline exception logs at ERROR via WebFlux's default handler — functionally correct, just
  noisier than P3-A/B's INFO-level equivalent).
- **Smoke C**: admission rejects in 6.7ms; connection-pool pending stayed 0. PASS.
- **Smoke D**: `CANCEL` correctly classified as `client_disconnect` by elimination (not by
  assumption) — cancellation reached Mock LLM within ~2ms of the client disconnecting. PASS.

## Tests

`./gradlew test` — 25 tests. Ported byte-identical from P3-B where the code is byte-identical
(`AdmissionGateTest`, `AbsoluteDeadlineTest`, `SseFrameFormatterTest`), rewritten for P3-C's
simpler `RequestLifecycle` (no external resources to race-close), plus P3-C-specific coverage:
`ChatControllerLifecycleWiringTest` (the outcome-classification wiring, including the required
race matrix from Unit 4 §27/§28 against fake upstream `Flux`es, no Spring context needed) and
`BackpressureAndDemandTest` (confirms the deadline and cancellation both work even when the
downstream subscriber never grants demand — Unit 4 §29; explicitly not a claim about WebClient's
own HTTP-layer buffering under a real slow network client, which is Unit 8 scope).

## P3-B / P3-C Structural Comparison (Unit 4 §36)

**Identical** (byte-for-byte, `diff` confirms): `EnvUtil.java`, `AdmissionGate.java`,
`AbsoluteDeadline.java`, `AbsoluteDeadlineExceededException.java`, `MockLlmClient.java`,
`SseFrameFormatter.java` (used here only for the `bytes_relayed` metric calculation, not for the
actual response body — see below), `WebClientConfig.java` (same `ConnectionProvider`/`LoopResources`
policy, deliberately).

**Different, by design (Experiment B's controlled variable):**

| | P3-B | P3-C |
|---|---|---|
| Server | Tomcat (Servlet) | Reactor Netty (WebFlux) |
| Response write path | `AsyncContext` + `PrintWriter` + dedicated Servlet write executor + `PerStreamWriteChannel` (bounded buffer, drain loop, lost-wakeup handling) | None — the response `Flux` itself, driven by WebFlux/Netty's own demand |
| SSE output | Manually reconstructed wire text (`SseFrameFormatter`, written via `PrintWriter`) | Native `ServerSentEvent<String>` passed straight to WebFlux's own SSE encoder — `SseFrameFormatter` reused only to size the `bytes_relayed` counter, never for actual output |
| Absolute deadline enforcement | Shared `ScheduledExecutorService` watchdog (authoritative) + `AbsoluteDeadline` operator (supplementary) | `AbsoluteDeadline` operator alone, wrapping the whole chain — no separate watchdog thread |
| `RequestLifecycle` fields | `requestId`, `startNanos`, `admitted`, `firstChunkRecorded`, `outcome` CAS, **`Disposable upstreamSubscription`**, **`ScheduledFuture deadlineTask`**, **`PerStreamWriteChannel writeChannel`** | `requestId`, `startNanos`, `admitted`, `firstChunkRecorded`, `outcome` CAS — nothing else |
| Controller return type | `void` (writes directly to `HttpServletResponse` via `AsyncContext`) | `Mono<ServerResponse>` via `RouterFunction` |
| "Upstream complete ≠ client received it" fix needed? | Yes — `markProducerDone()` (found as a real bug, Unit 2/3) | No — verified, not just architecturally assumed (Smoke A passed with the final event present on the first successful run) |
| Metrics | `gateway.*` + `servlet.write.*` + `reactor_netty_*` (client) | `gateway.*` + `reactor_netty_*` (client only — no server-side Reactor Netty metrics exposed by default, confirmed) |
| Thread pools observed | Tomcat `http-nio-*`, `servlet-write-*`, `gateway-webclient-loop-nio-*` (10) | `reactor-http-nio-*` (server, 5) + `gateway-webclient-loop-nio-*` (client, 10) — zero name overlap |

This table is the basis for Experiment B's variable control — everything in the "identical" list
stays constant so the "different" list is the only thing being compared.
