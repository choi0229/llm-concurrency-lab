# Unit 3 — P3-B Functional Smoke Test Evidence

Status: Unit 3 functional verification (not Formal/load testing)
Date: 2026-08-22

All runs used the real Mock LLM (`mock-llm-fastapi`, local `.venv`, port 8000) and the real
`gateway-mvc-webclient` module on Java 8 (Azul Zulu 8.96.0.205 native ARM64) + Spring Boot 2.7.18 +
Tomcat 9.0.83 + WebClient/Reactor Netty 1.0.39, per the runtime frozen in
`docs/decisions/phase3-version-compatibility.md`. Startup log confirms `Tomcat started on port(s):
8082` and no Netty *server* startup line — Reactor Netty is outbound-client-only, exactly as Unit 0
§4-2 already established for this dependency combination.

## Smoke A — normal stream

`smokeA-response-body.txt` / `smokeA-postflight-prometheus.txt`.

Request: `chunkCount=5, firstChunkDelayMs=300, chunkIntervalMs=150`. All 6 frames (5×`delta` +
1×`final`) arrived in order on the first try — the `markProducerDone()` fix from P3-A's Unit 2
Smoke A bug was ported *before* this run, so the final-frame-loss bug did not reproduce here.

**A second, real bug was found and fixed here** (not a repeat of P3-A's): the terminal log line for
a fast-completing stream (all frames already drained by the time WebClient's `onComplete` fires —
the common case, since Mock LLM paces frames slower than the write executor can drain them) showed
up on thread `reactor-http-nio-1`, not a `servlet-write-*` thread:

```
[before fix]
... [servlet-write-3] ...                                    <- normal case: drain thread finalizes
... reactor-http-nio-N ... terminal: outcome=completed        <- this run: reactor thread finalized instead
```

Root cause: `PerStreamWriteChannel.markProducerDone()`'s fast path (buffer already empty when
called) ran `lifecycle.tryTerminate("completed")` — which calls `AsyncContext.complete()`, a
Servlet-container operation — inline on whatever thread called `markProducerDone()`. Since that
method is explicitly the one thing a Reactor callback is allowed to call directly
(`docs/decisions/phase3-mvc-webclient-write-path.md` §3, Unit 3 §6), this meant Servlet-side
lifecycle work was silently running on a Reactor Netty event-loop thread — not a literal
`PrintWriter` call, so not a correctness bug, but a real boundary violation of the ADR's "Reactor
callback never does Servlet-side work" rule. Fixed by dispatching that fast path through the shared
write executor instead of running it inline (see the diff in `diff-PerStreamWriteChannel.txt` — the
one intentional divergence from P3-A's otherwise byte-identical copy of this class). Re-verified:
the terminal log now consistently shows a `servlet-write-*` thread.

First request TTFB was 615ms (higher than the 300ms `firstChunkDelayMs` configured) — a second,
warmed-up request measured TTFB at 314ms, in line with P3-A's ~385ms for the same parameters. The
gap is consistent with one-time Reactor Netty/connection-pool/JIT warmup cost on the very first
request, not a per-request cost — noted as an observation only; Unit 3 forbids drawing performance
conclusions.

All postflight gauges 0, `gateway_requests_total{outcome="completed"}` correct.  **PASS** (after
the fix above).

## Smoke B — absolute deadline vs. blocking read (direct P3-A comparison)

`smokeB-response-body.txt`, `smokeB-gateway-log-excerpt.txt`, `smokeB-mockllm-log-excerpt.txt`,
`smokeB-postflight-prometheus.txt`.

**Identical scenario to P3-A's Unit 2 Smoke B2** (same Mock LLM stall knob, same
`CHAT_TOTAL_TIMEOUT_MS=2500`): `firstChunkDelayMs=100, chunkIntervalMs=1000, stallAfterChunk=2,
stallMs=30000` — two real chunks arrive, then the upstream stalls for 30s, well past the 2.5s
deadline.

Measured (gateway log, millisecond timestamps):

```
request start:                14:28:55.232
terminal (outcome=timeout):   14:28:57.739   <- elapsed 2507ms, matches the 2500ms deadline almost exactly
```

Mock LLM's own log, independently:

```
14:28:57,739 INFO [...] client disconnected (stream closed by server)
```

**The cancellation reached Mock LLM in the same millisecond the deadline fired.** This is the
direct, measured counterpoint to P3-A's Unit 2 Smoke B finding: P3-A's deadline watchdog also fired
exactly on schedule (+2505ms) but the blocking `HttpURLConnection` worker thread did not actually
unblock until +3616ms — a ~1.1s lag, because `disconnect()`/`interrupt()` cannot promptly interrupt
a thread already blocked inside a native socket read. P3-B has no such lag because there is no
blocking read to begin with — `Subscription.cancel()` (issued from
`AbsoluteDeadline`/`RequestLifecycle.tryTerminate()`) propagates through Reactor Netty to the
actual TCP connection as part of the normal non-blocking cancellation path, not by asking an
unrelated mechanism to interrupt an in-progress blocking call.

Postflight: `gateway_upstream_active=0`, `gateway_admission_active=0`,
`reactor_netty_connection_provider_pending_connections=0`, `gateway_watchdog_activated_total=0`
(the safety watchdog never had to fire — the primary deadline mechanism worked). **PASS.**

## Smoke C — admission

`smokeC-*`. `CHAT_ADMISSION_LIMIT=1`, `WEBCLIENT_MAX_CONNECTIONS=1`. First request holds the sole
permit for ~4.4s, second fired 0.5s later.

Second request: `HTTP 503`, `{"status":"REJECTED","reason":"executor_saturated"}`, returned in
**4.6ms** — confirms non-blocking `tryAcquire()`, same as P3-A. Critically,
`reactor_netty_connection_provider_pending_connections=0` throughout — confirms the connection pool
never became a second, hidden capacity limiter behind admission (`docs/decisions/
phase3-admission-connection-pool.md` §3-5's invariant, verified functionally). **PASS.**

## Smoke D — client disconnect

`smokeD-*`. `curl --max-time 0.6` against a 20-chunk/500ms-interval stream (after a warmup
request).

```
14:30:10.504  request start
14:30:11.613  [servlet-write-7] client disconnected mid-stream (checkError)
14:30:11.613  [nio-8082-exec-4] async error: Broken pipe          <- container's own onError, same race as P3-A
14:30:11.615  [servlet-write-7] terminal: outcome=client_disconnect
```

Mock LLM's own log shows `client disconnected (stream closed by server)` at the same `14:30:11,615`
timestamp — cancellation reached the upstream essentially instantly, consistent with Smoke B's
finding. Two detection paths raced (`PrintWriter.checkError()` and the container's
`AsyncListener.onError`) exactly as designed — only one performed cleanup
(`gateway_requests_total{outcome="client_disconnect"}=1`, not 2). The same benign Spring MVC
`HttpMessageNotWritableException` log noise observed in P3-A's equivalent check appeared here too
(cosmetic, not functional). Postflight gauges all 0. **PASS.**

## Confirmed metric names (this run, `full-actuator-prometheus-output.txt`)

Common `gateway.*`/`servlet.write.*` — identical names to P3-A (as designed). New in P3-B (Reactor
Netty client + connection pool, all previously confirmed in Unit 1's spike, plus one new one this
run surfaced — `reactor_netty_http_client_address_resolver_seconds_*`, which appears because this
run used a hostname (`localhost`) requiring actual DNS resolution, unlike Unit 1's spike which used
a raw IP): full list of `reactor_netty_*` metric name prefixes is in
`full-actuator-prometheus-output.txt`.

## Thread census (`thread-name-census.txt`)

`jstack` on a running instance: 10× `reactor-http-nio-N` (Reactor Netty's default event-loop pool),
7× `servlet-write-N` (of the configured pool size 8 — `ThreadPoolExecutor` creates threads lazily),
10× `http-nio-8082-exec-N` (Tomcat), 2× `deadline-watchdog-N`. **No blocking-outbound pool exists**
— confirmed by absence, matching Unit 3 §22's "없는 executor를 0 Gauge로 꾸미지 않는다" design
choice in `GatewayMetrics`.
