# Phase 4 Unit 2 — Architecture Static Audit & M1/M2 Structural Parity

## Static architecture audit (docs/test-plan/phase4-design.md Unit 2 section 41)

| | M1 (platform-queue) | M2 (virtual-thread) | M3 (webflux) |
|---|---|---|---|
| `HttpURLConnection` | yes | yes | no |
| Platform `ThreadPoolExecutor` (outbound) | yes (bounded 50/500) | no | no |
| `VirtualThreadPerTaskExecutor` (outbound) | no | yes | no |
| Bounded outbound queue | yes (`ArrayBlockingQueue(500)`) | no (unbounded — no admission) | n/a |
| Servlet API (`jakarta.servlet.*`) | yes | yes | no |
| `AsyncContext` | yes | yes | no |
| `PrintWriter` | yes (via shared write executor) | yes (via shared write executor) | no |
| Application write executor | yes (shared, 64/20000) | yes (shared, 64/20000, byte-identical config) | no (none — native reactive response) |
| `WebClient` | no | no | yes |
| Reactor Netty Server | no (Tomcat) | no (Tomcat) | yes |

Confirmed via `grep -rn` over each module's `src/main/java` — see raw output captured during Unit 2
execution (only doc-comment mentions of the *other* model's tech appeared in `MockLlmClient.java`,
`BlockingMockLlmRelay.java`, `ChatTaskSubmitter.java` javadoc; zero actual imports/usages).

M3 F10 static audit (docs/test-plan/phase4-design.md Unit 2 section 37): `grep` over
`gateway-phase4-webflux/src/main/java` for `Thread.sleep`, `.block()`, `.blockFirst()`,
`.blockLast()`, `HttpURLConnection`, `jakarta.servlet`, `PrintWriter`, `boundedElastic`,
`Future.get()`/`.join()` found **zero** real usages (only javadoc mentions of what M3 avoids, and
`AtomicBoolean/AtomicReference/AtomicInteger.get()` calls, not `Future.get()`). Production jar
(`gateway-phase4-webflux-0.1.0.jar`) contains **zero** `tomcat-*`/`servlet-api-*` entries
(`unzip -l | grep`, exit code 1).

## M1/M2 structural diff (docs/test-plan/phase4-design.md Unit 2 section 42)

`com.llmconcurrencylab.phase4.common` package — **15 files, byte-identical** between
`gateway-phase4-platform-queue` and `gateway-phase4-virtual-thread`
(`diff -rq` exit code 0, confirmed after every source change during Unit 2):

```
BlockingMockLlmRelay.java   ChatController.java        ChatTaskSubmitter.java
DeadlineWatchdog.java       EnvUtil.java                GatewayMetrics.java
HealthController.java       MockLlmClient.java          Outcome.java
PerStreamWriteChannel.java  RequestIdGenerator.java     RequestLifecycle.java
SseFrame.java                WriteChannelMetrics.java   WriteExecutorConfig.java
```

This includes `ChatController` itself — the Servlet request handling, SSE relay orchestration,
absolute-deadline wiring, and terminal-outcome finalization are **identical source code** in M1
and M2. The only files that differ are the task-submission/executor-config seam
(`docs/test-plan/phase4-design.md` section 3's "single seam" principle, verified structurally, not
just claimed):

| M1-only (`platformqueue` package) | M2-only (`virtualthread` package) |
|---|---|
| `PlatformExecutorConfig.java` (bounded `ThreadPoolExecutor` bean) | `VirtualThreadExecutorConfig.java` (`VirtualThreadPerTaskExecutor` bean) |
| `PlatformTaskSubmitter.java` (queue-wait/task-duration instrumentation, cancel-hook = queue removal) | `VirtualTaskSubmitter.java` (no cancel-hook — no queue to remove from) |
| `PlatformExecutorMetrics.java` (`executor.*` metrics) | `VirtualTaskMetrics.java` (`virtual.tasks.*` metrics) |
| `Phase4PlatformQueueApplication.java` | `Phase4VirtualThreadApplication.java`, `VirtualThreadDiagnosticController.java` (F9 diagnostic only, not on the hot path) |

## M3 semantic parity (not source parity, docs/test-plan/phase4-design.md section 43)

Confirmed via live functional testing (this Unit): identical API (`POST /chat/stream`), identical
SSE event/data/order/final semantics as observed by an SSE parser (raw whitespace differs — no
space after `:` — expected and accepted per contract), identical absolute-deadline start point
(request body decode), identical outcome label set, identical common metric names/semantics.

## Reactor resource topology — real runtime observation (docs/test-plan/phase4-design.md
section 0-3), not assumed

`jcmd <pid> Thread.print` on a live M3 instance under a single in-flight request showed:

- `reactor-http-nio-1` .. `reactor-http-nio-10` — **10 threads**, matching this host's
  `Runtime.availableProcessors()`(=10, Unit 0 OS snapshot) — Reactor Netty's default event-loop
  group sizing. **Shared between the embedded server and the WebClient** (no custom
  `LoopResources` was configured — docs/test-plan/phase4-design.md section 0-3 — this sharing is
  the observed consequence of that decision, not something coded explicitly).
- `server` — the Reactor Netty boss/acceptor thread.
- `parallel-1` — Reactor's default `Schedulers.parallel()`, used internally by `Mono.delay()` for
  the absolute-deadline signal (`ChatHandler`'s deadline operator, section 3-3's "Candidate C"
  pattern) — not a thread Phase 4 code creates directly.
- `boundedElastic-1` / `boundedElastic-evictor-1` — Reactor's default `Schedulers.boundedElastic()`,
  present without any Phase 4 code invoking it explicitly (internal Reactor Netty/JDK use, e.g. DNS
  resolution) — recorded as observed fact, not further attributed.

Full raw dump: `thread-topology.txt` (also includes M1: 10 `http-nio-18101-exec-*` Tomcat threads +
1 `phase4-pt-outbound-1` (lazy-started, matches `prestartAllCoreThreads=false`) + 3
`phase4-write-*` + 1 `phase4-deadline-watchdog`; M2: same Tomcat pattern + JDK's own virtual-thread
I/O poller threads `Read-Poller`/`Read-Updater`/`Write-Poller`/`Write-Updater`/
`VirtualThread-unparker` — direct runtime evidence that blocking I/O is actually running on virtual
threads under the hood, and notably **no** `phase4-pt-outbound-*` thread exists in M2, confirming
no bounded platform-thread pool backs M2's outbound calls).
