# gateway-mvc-blocking-spring5 (P3-A)

Phase 3 Unit 2 — the blocking-outbound Servlet control. Java 8 + Spring Boot 2.7.18 + Spring MVC
(Tomcat, embedded) + `java.net.HttpURLConnection`. This is the functional baseline that P3-B
(Unit 3, MVC + WebClient) and P3-C (Unit 4, WebFlux end-to-end) will be compared against —
see `docs/test-plan/phase3-design.md` for the research questions and `docs/decisions/
phase3-*.md` for the frozen contracts this module implements.

**Scope note:** this module implements *functional correctness* only. Pool sizes, buffer
capacities, and the admission ceiling below are **functional defaults, not Formal-frozen values**
— they exist so the module runs, not because they've been sized against real load. Formal sizing
happens before Unit 5 screening (see the ADRs); do not read these numbers as benchmark-ready.

## Architecture

```
Front -> Spring MVC Controller -> Servlet AsyncContext -> admission tryAcquire()
  -> blocking outbound ThreadPoolExecutor -> HttpURLConnection -> Mock LLM SSE
  -> BufferedReader/readLine -> SSE frame parser -> PerStreamWriteChannel
  -> shared bounded Servlet write executor -> PrintWriter.write + flush -> Front
```

The blocking outbound executor (runs the `HttpURLConnection` call) and the Servlet write executor
(runs `PrintWriter` writes) are deliberately separate resources — see
`docs/decisions/phase3-mvc-webclient-write-path.md` §2. A shared `ScheduledExecutorService`
watchdog enforces one absolute deadline per request (`docs/decisions/phase3-timeout-cancellation.md`).

## Running

Requires the Unit 0 frozen runtime (`docs/decisions/phase3-version-compatibility.md`): Azul Zulu
JDK 8 native ARM64. The Gradle wrapper is pinned to 7.6.6.

```bash
JAVA_HOME=<path to Zulu 8 home> ./gradlew bootJar
MOCK_LLM_BASE_URL=http://localhost:8000 JAVA_HOME=<...> \
  java -jar build/libs/gateway-mvc-blocking-spring5-0.1.0.jar
```

Mock LLM (separate process, `mock-llm-fastapi/`) must be running first — this module has no
built-in fallback.

## Endpoints

- `POST /chat/stream` — SSE relay, same request/response contract as Phase 1/2
  (`docs/test-plan/phase3-design.md` §3). Body is passed through to Mock LLM unchanged.
- `GET /healthz` — `{"status":"ok"}`.
- `GET /actuator/health`, `GET /actuator/prometheus` — Micrometer/Actuator.

## Config (env vars)

| Var | Functional default | Meaning |
|---|---|---|
| `SERVER_PORT` | 8080 | |
| `MOCK_LLM_BASE_URL` | `http://localhost:8000` | |
| `CHAT_ADMISSION_LIMIT` | 50 | Application admission ceiling (`Semaphore.tryAcquire()`) |
| `CHAT_BLOCKING_POOL_SIZE` | = `CHAT_ADMISSION_LIMIT` | Blocking outbound executor core=max size |
| `CHAT_CONNECT_TIMEOUT_MS` | 3000 | Clamped to remaining deadline budget at connect time |
| `CHAT_READ_TIMEOUT_MS` | 30000 | Clamped to remaining deadline budget at connect time |
| `CHAT_TOTAL_TIMEOUT_MS` | 60000 | Absolute deadline, request-received to final write |
| `CHAT_ASYNC_WATCHDOG_MARGIN_MS` | 5000 | `AsyncContext.setTimeout()` = total + margin, safety-net only |
| `SERVLET_WRITE_POOL_SIZE` | 8 | Shared write executor size |
| `SERVLET_WRITE_QUEUE_CAPACITY` | 64 | Shared write executor bounded queue |
| `SERVLET_PER_STREAM_BUFFER_CAPACITY` | 32 | Per-request write buffer, bounded |
| `LOG_LEVEL` | INFO | |

## Metrics

Micrometer-only (`docs/decisions/phase3-metrics-contract.md` §1), exposed at
`/actuator/prometheus`. Common (`gateway.*`) + P3-A-specific (`outbound.blocking.*`) + shared
Servlet write (`servlet.write.*`) — see that ADR for the full table. `gateway.write.overflow` is
used as the single counter for both the "common" and "P3-A/B servlet write" overflow entries in
that ADR (they describe the same event).

## Functional smoke verification

`docs/test-results/phase3/unit2-p3a-functional/SUMMARY.md` has the full results. Highlights:

- **Smoke A** (normal stream): found and fixed a real bug where the terminal `final` SSE frame
  could be silently dropped (see `PerStreamWriteChannel.markProducerDone()`). Fixed, regression
  tests added, re-verified — PASS.
- **Smoke B** (absolute deadline): confirmed the deadline watchdog fires exactly on schedule, but
  found — by direct measurement, not assumption — that `HttpURLConnection.disconnect()` and
  `Thread.interrupt()` do **not** promptly unblock a `readLine()` call already blocked in a native
  socket read on this JDK. The mechanism that actually bounds an in-flight blocked read is that
  read's own once-clamped socket timeout, which can let a late-starting stall overrun the
  configured deadline. Documented as a structural P3-A risk, not patched (no fix exists within
  plain `HttpURLConnection` — verified with a separate spike). See the SUMMARY for the measured
  numbers.
- **Smoke C** (admission): `CHAT_ADMISSION_LIMIT=1`, second concurrent request rejected in 6.7ms
  (503, `executor_saturated`) — confirms non-blocking `tryAcquire()` semantics. PASS.
- Client disconnect: verified manually in addition to the required unit tests — exactly-once
  terminal outcome under a real race between `PrintWriter.checkError()` and the container's
  `AsyncListener.onError`. PASS.

All smoke runs end with every postflight gauge (`gateway_active_streams`,
`gateway_admission_active`, `gateway_upstream_active`, `outbound_blocking_executor_active`,
`servlet_write_executor_active`, `servlet_write_stream_buffered_frames`) at 0.

## Tests

`./gradlew test` — 29 unit tests covering the serialized writer (ordering, concurrent-write
exclusion, overflow, executor rejection, lost-wakeup stress test across 30 repeated iterations,
completion-drain-before-terminate), `RequestLifecycle` (exactly-once terminal/outcome/permit/
disconnect/AsyncContext-completion under concurrent races), admission (non-blocking reject), and
the SSE frame reader.
