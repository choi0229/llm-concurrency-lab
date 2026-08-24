# Unit 4 — P3-C Functional Smoke Test Evidence

Status: Unit 4 functional verification (not Formal/load testing)
Date: 2026-08-22

All runs used the real Mock LLM (`mock-llm-fastapi`, local `.venv`, port 8000, addressed via
`127.0.0.1` per this Unit's §18 DNS policy) and the real `gateway-webflux` module on Java 8 (Azul
Zulu 8.96.0.205 native ARM64) + Spring Boot 2.7.18 + WebFlux/Reactor Netty 1.0.39. `startup-log.txt`
confirms `NettyWebServer` starts (no Tomcat startup line anywhere), and a static grep
(`grep -rnE "javax\.servlet|jakarta\.servlet" src/main/java/`) confirms zero Servlet API references
in main source.

## A handler-model / API integration mismatch found and corrected before Smoke A could pass

Not a claimed Spring Framework defect — this is a mismatch between the annotated-`@RequestMapping`
handler model and the `ServerResponse` return type, found by running functional Smoke A, not by
inspection. The module would not serve a single successful request until this was corrected.

1. **First attempt: `Mono<ServerResponse>` from an `@RestController`-annotated method.**
   `@RestController` implies `@ResponseBody`, which routes every return value through WebFlux's
   generic message-writer resolution (Jackson by default). That resolution does not special-case
   `ServerResponse` — it tried to serialize the `ServerResponse` **object itself** as JSON:
   `CodecException: ... No serializer found for class
   DefaultEntityResponseBuilder$DefaultEntityResponse`, logged *after* the 200 OK had already
   committed, connection aborted mid-stream (`curl: (18) transfer closed with outstanding read data
   remaining`).
2. **Second attempt: plain `@Controller` (no implicit `@ResponseBody`).** Different failure:
   `IllegalStateException: Could not resolve view with name 'chat/stream'` — the annotated
   `@RequestMapping` return-value handling in this configuration did not route a bare
   `ServerResponse` return value to `ServerResponseResultHandler`, and fell through to Spring's
   default view-name resolution (which defaults the view name to the request path when nothing
   else claims the result).

**Correction:** switched to WebFlux's functional routing model — `@Bean RouterFunction<ServerResponse>`
wiring a plain `stream(ServerRequest)` handler method. This is `ServerResponse`'s native,
unambiguous use case (the WebFlux reference model this type is designed for); Smoke A passed
cleanly on the first run after this change. See `ChatController`'s javadoc for the full account,
kept in the shipped code as a record of why the annotated-controller approach was not used.

## Smoke A — normal stream

`smokeA-response-body.txt` / `smokeA-response-headers.txt` / `smokeA-postflight-prometheus.txt`.

Request: `chunkCount=5, firstChunkDelayMs=300, chunkIntervalMs=150`. All 6 events (5×`delta` +
1×`final`) arrived in order, correct payloads. **No `markProducerDone()`-equivalent was needed** —
unlike P3-A/B, there is no separate write buffer for a final event to get stranded in; WebFlux's
`doOnComplete` on the response chain only fires once every emitted item has actually been handed to
the downstream writer, and there's nothing else downstream of that in this architecture. Confirmed
empirically, not just by architectural argument: the `final` event was present on the very first
successful run, with no equivalent bug to chase.

One cosmetic, spec-compliant difference from P3-A/B's output: WebFlux's `ServerSentEventHttpMessageWriter`
emits `event:delta` (no space after the colon) where Mock LLM/P3-A/B emit `event: delta` (with a
space). The SSE spec treats a single leading space after the colon as optional/trimmed by
compliant parsers, so this is not a contract violation (`docs/test-plan/phase3-design.md` §4
explicitly only requires identical event sequence/payload, not byte-identical framing).

First-request TTFB was 493ms (vs. the 300ms configured `firstChunkDelayMs`); a second, warmed-up
request measured 313ms, matching P3-B's own warmup pattern. Noted as an observation only — no
performance conclusion drawn (Unit 4 §37 forbids it at this stage).

Postflight: all gauges 0, `gateway_requests_total{outcome="completed"}=1`. **PASS** (after the two
fixes above).

## Smoke B — absolute deadline (direct three-way comparison point)

`smokeB-response-body.txt`, `smokeB-gateway-log-excerpt.txt`, `smokeB-postflight-prometheus.txt`.

**Identical scenario to P3-A's Unit 2 Smoke B2 and P3-B's Unit 3 Smoke B**: `firstChunkDelayMs=100,
chunkIntervalMs=1000, stallAfterChunk=2, stallMs=30000`, `CHAT_TOTAL_TIMEOUT_MS=2500`.

```
client-observed total time: 2.524s   (deadline configured: 2.500s)
Mock LLM's own log: "client disconnected (stream closed by server)" at the same
  wall-clock instant the gateway's terminal outcome was recorded (~2.5s after request start)
```

Matches P3-B's result (2.512s, prompt cancellation) and stands in the same contrast to P3-A's
result (3.616s — the ~1.1s blocking-read lag documented in Unit 2). P3-C's cancellation path is
architecturally the simplest of the three: `AbsoluteDeadlineExceededException` propagates through
`doOnError`, Reactor cancels the upstream WebClient subscription as a normal consequence of the
`takeUntilOther`-based operator (verified in Unit 1), and that cancellation reaches Mock LLM with
no intermediate buffering layer to cross.

One log-noise observation: because the `AbsoluteDeadlineExceededException` propagates all the way
to WebFlux's `HttpWebHandlerAdapter` (there is no local `onErrorResume` swallowing it before that
point), it gets logged at **ERROR** with a full stack trace (`Error ... but ServerHttpResponse
already committed`) even though this is the fully-expected, correctly-handled deadline path — not a
functional defect (the terminal outcome is still recorded exactly once as `timeout`, and cleanup is
complete), but noisier than P3-A/B's INFO-level handling of the equivalent case. Recorded as an
observed characteristic, not fixed in this Unit (Unit 4 §41 does not require log-level tuning as a
pass criterion; flagged here for whoever picks up log-noise cleanup ahead of Formal).

Postflight: `gateway_upstream_active=0`, `gateway_admission_active=0`,
`reactor_netty_connection_provider_pending_connections=0`. **PASS.**

## Smoke C — admission

`smokeC-*`. `CHAT_ADMISSION_LIMIT=1`, `WEBCLIENT_MAX_CONNECTIONS=1`.

Second request: `HTTP 503`, `{"status":"REJECTED","reason":"executor_saturated"}`, in **6.7ms** —
matches P3-A (6.7ms) and P3-B (4.6ms), confirms non-blocking `tryAcquire()` resolved before any
`Mono`/`Flux` was built (Unit 4 §7). `reactor_netty_connection_provider_pending_connections=0`
throughout. **PASS.**

## Smoke D — client disconnect (CANCEL classification verified, not assumed)

`smokeD-*`. `curl --max-time 0.6` against a 20-chunk/500ms-interval stream.

```
14:58:23.712  request start
14:58:24.313  terminal: outcome=client_disconnect      <- gateway
14:58:24,315  "client disconnected (stream closed by server)"   <- Mock LLM's own log, same instant
```

Single detection path (`doOnCancel` on the response chain — there is no second, racing detection
mechanism the way P3-A/B have `checkError()` + `AsyncListener.onError`, because there's no Servlet
layer to generate a second signal). Per Unit 4 §12's explicit warning against blindly mapping every
`CANCEL` signal to `client_disconnect`: in this pipeline's specific shape, admission rejection never
reaches this Flux (resolved before it exists) and the deadline surfaces via `doOnError`
(`AbsoluteDeadlineExceededException`), not `doOnCancel` — so a `CANCEL` reaching this chain has
exactly one remaining explanation (the Front connection going away), not an unverified assumption.
`gateway_requests_total{outcome="client_disconnect"}=1`, postflight gauges 0. **PASS.**

## Actual Reactor Netty metric names confirmed this run (`full-actuator-prometheus-output.txt`)

Client-side (identical prefixes to P3-B, confirmed again here):
`reactor_netty_connection_provider_*`, `reactor_netty_http_client_*`,
`reactor_netty_eventloop_pending_tasks{name="gateway-webclient-loop-nio-N"}`,
`reactor_netty_bytebuf_allocator_*`.

**No `reactor_netty_http_server_*` metrics exist** — confirmed by full-output grep, not assumed.
Boot's default embedded `NettyWebServer` does not call `.metrics(true)` on its underlying
`HttpServer` the way this module's `WebClientConfig` explicitly does for the outbound client; no
customization was added to change this for Unit 4 (out of scope — a genuine "what P3-C's server
side does NOT expose by default" finding, recorded honestly rather than invented). What IS present
for server-side request timing is Micrometer's own generic `http_server_requests_seconds_*`
(Boot's standard WebFlux instrumentation, unrelated to Reactor Netty's own metric namespace) and
`executor_*` (Boot's default `applicationTaskExecutor` bean — unrelated to this module's chat
pipeline; nothing in this codebase creates or names an executor).

**Confirmed absent, as required (Unit 4 §23):** no `tomcat_*`, no `servlet_*`, no
`outbound_blocking_*` — grep against the full output returns nothing.

## Thread census (`thread-name-census.txt`)

`jstack` on a running instance: 10× `gateway-webclient-loop-nio-N` (the explicit dedicated client
pool from Unit 4-0), 5× `reactor-http-nio-N` (the WebFlux **server**'s own default event loop —
note: fewer than the client's 10, Boot's embedded-server bootstrap sizes its default loop
differently from Reactor Netty's own global-default formula; not investigated further, out of
scope), 1× `server` (the Netty boss/acceptor thread), plus Reactor's always-present global
`boundedElastic`/`parallel` schedulers (unused by this module's own code — Reactor Core initializes
them unconditionally). **Zero name overlap between client and server thread pools** — confirms the
Unit 4-0 `LoopResources` separation is actually in effect at the running-application level, not
just in configuration.
