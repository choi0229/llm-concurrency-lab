# ADR: Phase 3 Admission Semantics Unification (Unit 6)

Status: Accepted (Unit 6 scope, amends `docs/decisions/phase3-admission-connection-pool.md` §1)
Date: 2026-08-23

## 1. Problem

Unit 5.5's Slow Client diagnostic (`docs/test-results/phase3/unit5.5-diagnostics/SUMMARY.md` D2)
measured that P3-C's `terminal: outcome=completed` (and therefore admission permit release) fired
169ms after request start in a 60-chunk slow-client run, while the client took 9.32s to actually
finish receiving the data. Reading P3-A/B's actual code (not assumed) showed the reverse emphasis:
their admission permit for the `completed` outcome was held until the Servlet
`PerStreamWriteChannel` finished draining every buffered frame to the client — i.e. much closer to
(though still not identical to) full response delivery.

This meant the same `CHAT_ADMISSION_LIMIT` value did not necessarily bound the same physical
resource across the three implementations. Before Unit 6 Screening starts comparing P3-A/B/C under
load at a shared admission ceiling, this ambiguity had to be resolved — not carried forward as an
unstated assumption.

## 2. Audit — actual code, all three implementations

| | P3-A | P3-B | P3-C |
|---|---|---|---|
| Permit acquire | `admissionGate.tryAcquire()` in `ChatController.stream()`, before the deadline watchdog is scheduled, before any upstream call (`ChatController.java`) | same call site/shape | `admissionGate.tryAcquire()` inside `flatMap(requestBody -> ...)`, before the upstream/response `Flux` is built (`ChatController.java`) |
| Upstream start | `mockLlmClient.openStream()` on the blocking outbound executor, inside the `relay()` task | WebClient `Flux` built + subscribed synchronously inside `relay()`, same Servlet thread that handled `stream()` | `mockLlmClient.openStream()` wrapped in `Flux.defer()`; actual subscription happens when WebFlux subscribes the response body |
| Upstream natural EOF | `frameReader.readFrame()` returns `null` in the `relay()` loop | Reactor `onComplete` callback (`onUpstreamComplete()`) | `doOnComplete` on the `upstream` operator chain in `attachLifecycle()` |
| Response-side terminal (pre-fix) | Deferred: `writeChannel.markProducerDone()` at EOF, but `tryTerminate("completed")` fires only once `PerStreamWriteChannel`'s buffer actually drains via the shared write executor | same as P3-A (shared `PerStreamWriteChannel`) | Same signal as "upstream natural EOF" — no separate response-buffering layer exists in this architecture |
| Permit release (pre-fix) | Inside `RequestLifecycle.tryTerminate()`, i.e. at response-side drain-complete for the `completed` outcome — **not** at upstream EOF | same as P3-A | Inside `RequestLifecycle.tryTerminate()`, called directly from `doOnComplete`/`doOnError`/`doOnCancel` — i.e. already at upstream terminal, since there is no separate response terminal |
| Client physical receive completion observable by Gateway? | No. `PrintWriter.checkError()` only detects a write error, not confirmed delivery — but `PrintWriter.write()+flush()` blocks on the underlying `OutputStream`, so OS-level socket-buffer backpressure (not confirmed delivery) does propagate into `servlet_write_duration_seconds` before the drain loop advances | same as P3-A (identical write path) | No. Reactor Netty's outbound write is asynchronous; nothing in this code path blocks on or observes the Netty channel's actual flush/ack |

**Divergence found**: for every terminal outcome *except* `completed`, P3-A/B already released the
permit at the moment the upstream connection/subscription was torn down (timeout watchdog,
upstream I/O error, client disconnect, write overflow, internal error all call `tryTerminate()`
directly, which disconnects/disposes the upstream resource as part of the same call) — i.e.
upstream-lifetime-aligned already. The **only** divergence was the `completed` happy path, where
P3-A/B deliberately deferred permit release to response-drain-complete (a Unit 2 fix for a
different bug — not losing the final buffered SSE frame — that permit release happened to ride
along with), while P3-C released it at upstream complete because that is structurally the same
signal as "response terminal" in a design with no separate write-buffering layer.

Since `completed` is the common case under normal (non-degenerate) load — exactly the population
Unit 6 Screening exercises — this one-outcome divergence was not a corner case to note and move
past; it directly affects what `CHAT_ADMISSION_LIMIT=N` means during Screening/Formal.

## 3. Original ambiguity

Admission's intended meaning was never cleanly separated in Unit 1: "limit how long the Gateway
holds a request's full response lifecycle" and "limit how many requests may have an upstream Mock
LLM call in flight" had been treated as interchangeable, because in every scenario exercised
through Unit 5 (fast, non-degenerate clients) the two are numerically indistinguishable — the gap
between "upstream done" and "response fully drained" is single-digit milliseconds. Slow Client was
the first workload where the gap became large enough (seconds) to expose that these are two
different resources.

## 4. Decision

Phase 3 Primary admission (`gateway_admission_active`) is defined as a **downstream
upstream-call-lifetime limiter**:

```
permit acquire:  immediately before the upstream Mock LLM call/subscription starts
permit release:  at the first of — upstream normal complete, upstream error,
                  upstream cancel (via application timeout), upstream cancel (via
                  client disconnect propagating to the upstream subscription/connection)
```

It does **not** wait for client physical receive completion, in any of the three implementations.

The Gateway's response lifecycle (whether the application is still working on a request's HTTP
response at all — regardless of whether that means waiting on Mock LLM or waiting on a slow
client's write) is tracked separately as `gateway_active_streams` (§6).

## 5. Why this boundary, not another

- **Matches Phase 2's original purpose.** Phase 2's admission control (`ChatExecutorConfig`'s
  `core=10,max=50,SynchronousQueue,AbortPolicy`; `VirtualLimitedTaskSubmitter`'s
  `Semaphore(50).tryAcquire()`) existed to protect downstream LLM call concurrency, not to bound how
  long a Gateway holds an HTTP response open. Unifying on upstream-call lifetime restores that
  original intent for Phase 3.
- **The most architecture-neutral boundary across P3-A/B/C.** "Upstream call in flight" is a concept
  all three implementations already model explicitly (`HttpURLConnection` open→close, WebClient
  subscribe→complete/error/cancel) regardless of server model. "Response fully, physically delivered
  to the client" is not something any of the three implementations can observe — P3-A/B's blocking
  `PrintWriter.flush()` at least reflects OS-level socket-buffer backpressure, but that is still not
  confirmed delivery, and P3-C has no equivalent signal at all (Unit 5.5 D2, and Unit 4 §5 forbids
  adding one solely to manufacture this signal, since doing so would remove the reactive model's own
  advantage that Experiment B exists to measure).
- **Client physical receive completion is intentionally excluded from the boundary** for exactly the
  reason above: no common, architecture-neutral way to observe it exists, so using it as the
  admission boundary would silently favor whichever implementation happens to expose the closest
  proxy signal (P3-A/B) over one that structurally cannot (P3-C) — a false-parity risk, not a
  genuine one.

## 6. Metric canonical semantics (freeze)

### `gateway_admission_active`

- **Definition**: number of requests currently holding a Mock LLM upstream-capacity permit.
- **Start**: upstream call/subscription start (immediately after `admissionGate.tryAcquire()`
  succeeds and the upstream call is about to begin).
- **End**: upstream complete / upstream error / upstream cancel (deadline-triggered or
  disconnect-triggered).
- **Protects**: downstream Mock LLM concurrency — the resource Phase 2 admission control was
  originally built to protect.

### `gateway_active_streams`

- **Definition**: number of requests whose Gateway application response lifecycle has not yet
  reached a terminal outcome.
- **Start**: application streaming lifecycle start (`metrics.activeStreamsIncrement()`, right after
  admission succeeds).
- **End**: terminal outcome determination — `completed`, `timeout`, `upstream_error`,
  `client_disconnect`, `write_overflow`, or `internal_error` (i.e. `RequestLifecycle.tryTerminate()`
  completing, unchanged from Unit 1-5).

### Relationship

```
admission_active <= active_streams   (generally)
admission_active == 0 && active_streams > 0   is a normal, expected state
```

This is expected and pronounced under slow-client conditions specifically: Mock LLM upstream can
finish sending its `final` event while the Gateway is still draining buffered frames to a slow
client (P3-A/B) or while Reactor Netty is still physically flushing already-accepted bytes over the
wire (P3-C). **This is not a leak or a measurement error** — it is the intended consequence of
separating "is downstream capacity still held" from "is the Gateway still working on this
request's response." `admission_active == 0` must never be read as "all client responses for those
requests have finished" — see §8 (Unit 6 §25) for the exact same warning applied to Screening
interpretation.

For P3-C specifically: because there is no separate response-buffering layer (Unit 4 §9), the
`completed` outcome's `admission_active` and `active_streams` decrements currently fire from the
same `doOnComplete` callback — i.e. no observable timing gap exists between them *for that one
implementation, for that one outcome*, unlike P3-A/B where a real (if usually small) drain gap
exists. This is documented, not hidden: it is the honest consequence of P3-C's architecture, not a
sign the fix was incompletely applied there. See `RequestLifecycle.releaseAdmissionPermit()`'s
javadoc in `gateway-webflux` for the same note in code.

## 7. Non-goal

This ADR does not attempt to solve slow-client memory/backpressure risk with
`CHAT_ADMISSION_LIMIT` — that is a different resource-control problem, already owned by
P3-A/B's bounded per-stream write buffer + fail-fast overflow (`docs/decisions/
phase3-mvc-webclient-write-path.md`) and by Reactor Netty's own demand-driven backpressure for
P3-C. Admission's job, after this ADR, is exclusively to bound concurrent Mock LLM upstream calls.

## 8. What did NOT change

- `completed` / `timeout` / `upstream_error` / `client_disconnect` / `rejected` / `write_overflow` /
  `internal_error` outcome definitions — unchanged (§5 of `docs/test-plan/phase3-design.md`).
- `gateway_requests_started_total` population — unchanged.
- The client-observable SSE contract — unchanged.
- Absolute deadline semantics — unchanged.
- Servlet write-path architecture (P3-A/B) and reactive response structure (P3-C) — unchanged; no
  executor sizing, queue sizing, WebClient architecture, or write-path structural change was made
  anywhere in this fix.
- Exactly-once terminal-outcome accounting (`RequestLifecycle.tryTerminate()`'s CAS) — unchanged;
  admission-permit release now has its own independent exactly-once CAS
  (`RequestLifecycle.releaseAdmissionPermit()`), but the terminal-outcome CAS itself was not touched.

## 9. Code changes (all three implementations)

`RequestLifecycle` in all three modules gained:

```java
private final AtomicBoolean permitReleased = new AtomicBoolean(false);

public void releaseAdmissionPermit() {
    if (admitted && permitReleased.compareAndSet(false, true)) {
        admissionGate.release();
        metrics.admissionActiveDecrement();
    }
}
```

`tryTerminate()`'s previous inline `if (admitted) { admissionGate.release(); ... }` block was
replaced with a call to `releaseAdmissionPermit()` — idempotent, so calling it a second time from
`tryTerminate()` after the natural-EOF path already released the permit is always a safe no-op.

Call sites added at natural upstream EOF (the only path whose timing changed):

- **P3-A** (`ChatController.relay()`): `lifecycle.releaseAdmissionPermit()` immediately before
  `writeChannel.markProducerDone()`, at the point the frame-reading loop naturally ends.
- **P3-B** (`ChatController.onUpstreamComplete()`): `lifecycle.releaseAdmissionPermit()`
  immediately before `writeChannel.markProducerDone()`, called from the WebClient subscription's
  `onComplete` callback (method signature extended to take `RequestLifecycle` as a parameter).
- **P3-C** (`ChatController.attachLifecycle()`): `lifecycle.releaseAdmissionPermit()` called
  explicitly inside `doOnComplete`, immediately before `lifecycle.tryTerminate("completed")` —
  structurally symmetric with P3-A/B's call site even though (§6) it does not currently produce an
  observable timing gap for this one implementation.

## 10. Consequence

- P3-A/B: a request's admission permit may now be released while a Servlet write is still pending
  (buffer not yet fully drained to the client) — `gateway_active_streams` stays elevated until that
  drain completes.
- P3-C: a request's admission permit may be released while Reactor Netty is still physically
  flushing already-accepted bytes to a slow client — same `active_streams` behavior.
- In all three, a new upstream-bound request can now be admitted slightly sooner after a prior
  request's Mock LLM call finishes, even if that prior request's client-facing response has not
  fully drained yet. This is the intended effect, not a side effect to work around.
