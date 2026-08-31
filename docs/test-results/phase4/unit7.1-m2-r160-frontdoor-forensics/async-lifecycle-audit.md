# M2 R=160 (screen1) — AsyncContext / Request-Thread Lifetime Audit

Source: `gateway-phase4-virtual-thread/src/main/java/com/llmconcurrencylab/phase4/common/
ChatController.java` (shared M1/M2 controller — byte-identical between the two modules per its own
class javadoc; only the injected `ChatTaskSubmitter` implementation differs).

## Sequence, as written (`ChatController.stream()`, lines 60–136)

1. `metrics.requestStarted()` — increments `gateway_requests_started_total`. **First statement in
   the method.**
2. `AsyncContext asyncContext = request.startAsync();` — hands the request off to async processing.
   Per Servlet 3.0+ semantics, this is what allows the container (Tomcat) thread to be released back
   to its pool once the servlet method returns, rather than being held for the full response
   lifetime.
3. `asyncContext.setTimeout(...)`, response headers set, `AsyncListener` registered (onComplete /
   onTimeout / onError — this is where `Outcome.CLIENT_DISCONNECT`/`Outcome.TIMEOUT` would be
   recorded if the container detects either condition).
4. `response.getWriter()` obtained synchronously (line 104) — the one blocking-ish call still on the
   Tomcat thread, but this is a local buffer/writer object acquisition, not a network wait.
5. `taskSubmitter.trySubmit(lifecycle, task)` (line 131) — for M2, this is `VirtualTaskSubmitter`,
   which calls `metrics.taskStarted()` (→ `virtual_tasks_started_total`) synchronously and then
   `executor.execute(...)` on the virtual-thread-per-task executor, returning immediately (`execute`
   does not block on task completion).
6. Method returns. The actual relay work (`relay.relay(...)`, the 7–28s streaming lifetime) happens
   entirely inside the virtual thread the executor just spawned — **not on the Tomcat connector
   thread**.

## Conclusion: the Tomcat thread is held only for steps 1–5, not for the stream lifetime

Steps 1–5 involve no network I/O and no blocking wait — they are pure in-process object setup and a
non-blocking `execute()` call. This is directly confirmed by the measured
`tomcat_servlet_request_seconds_sum / _count ≈ 96 microseconds average` (`tomcat-metrics-audit.md`):
if the Tomcat thread were held for the full stream duration, this average would be in the same 7–28s
range as `gateway_request_duration_seconds`, not 96µs.

**Implication for Little's Law-style reasoning:** it is a category error to treat
`160 req/s × ~8s ≈ 1280` as a *Tomcat connector thread* requirement. That figure describes the
steady-state population of **virtual threads** (a resource M2's design explicitly does not bound —
`VirtualThreadExecutorConfig`'s own javadoc: "No application admission ceiling, no bounded task
queue"), not Tomcat's 200-thread connector pool, which only ever needs to hold a thread for
microseconds per request regardless of how many concurrent streams are in flight. This is consistent
with `tomcat_threads_busy_threads=1` being observed even while `virtual_tasks_active` sat at
800–1450 concurrently mid-run — two entirely different, correctly-decoupled resource pools.

This lifecycle analysis is what rules out `maxThreads=200` as a plausible bottleneck independent of
the metric-matching evidence in `tomcat-metrics-audit.md` — the two lines of evidence (source-code
lifecycle + runtime metric values) corroborate each other.
