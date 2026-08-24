# Unit 2 — P3-A Functional Smoke Test Evidence

Status: Unit 2 functional verification (not Formal/load testing)
Date: 2026-08-22

All runs used the real Mock LLM (`mock-llm-fastapi`, local `.venv`, port 8000) and the real
`gateway-mvc-blocking-spring5` module built and run on Java 8 (Azul Zulu 8.96.0.205 native ARM64) +
Spring Boot 2.7.18 + Tomcat 9.0.83, per the runtime frozen in
`docs/decisions/phase3-version-compatibility.md`.

## Smoke A — normal stream

`smokeA-response-body.txt` / `smokeA-response-headers.txt` / `smokeA-postflight-prometheus.txt`.

Request: `chunkCount=5, firstChunkDelayMs=300, chunkIntervalMs=150`.

**First run found a real bug**: the client never received the `event: final` frame even though
the server recorded `outcome=completed`. Root cause: `ChatController.relay()` called
`lifecycle.tryTerminate("completed")` synchronously on natural EOF, and `tryTerminate`'s cleanup
(`PerStreamWriteChannel.markTerminal()`) discarded whatever was still sitting in the write buffer
— including the `final` frame, which had only just been `offer()`'d and had not yet actually been
written by the (asynchronous) write executor. Fixed by adding
`PerStreamWriteChannel.markProducerDone()`: natural EOF now only signals "no more frames will be
offered"; the write channel itself calls `tryTerminate("completed")` once its buffer has actually
drained to empty, from inside the drain loop. Regression tests added:
`PerStreamWriteChannelTest#completionWaitsForPendingFramesToDrainBeforeTerminating` and
`#producerDoneWithEmptyBufferFinalizesImmediately`.

After the fix: all 6 frames (5×`delta` + 1×`final`) arrive in order, TTFB ≈ 385ms (≈ the configured
300ms `firstChunkDelayMs` + overhead), full stream ≈ 987ms. `gateway_requests_total{outcome="completed"}=1`,
all postflight gauges 0. **PASS.**

## Smoke B — absolute deadline vs. blocking read

Two variants were run; the first was inconclusive by construction, the second gives an unambiguous
answer.

### B1 (`smokeB-response-*`) — naive variant

`CHAT_TOTAL_TIMEOUT_MS=2500`, Mock LLM `firstChunkDelayMs=20000` (nothing ever arrives before the
deadline). Client cut off at ~2.58s — looks like a clean pass, but this variant can't actually tell
whether the watchdog's `disconnect()` woke the blocked read or whether the *first* read's own
remaining-budget-clamped socket timeout (also ≈2500ms, since it's clamped once at connect time to
the deadline) expired on its own at essentially the same instant. Both mechanisms are confounded
for a stall that starts at t≈0.

### B2 (`smokeB2-*`, `smokeB2-gateway-log-excerpt.txt`) — isolates the two mechanisms

Uses Mock LLM's `stallAfterChunk`/`stallMs` diagnostic knob (the same one Phase 1 built for this
exact purpose) so the stall starts *after* two real chunks have already arrived — the blocking read
that stalls therefore starts its own fresh read-timeout window at t≈1.1s, not t≈0.
`CHAT_TOTAL_TIMEOUT_MS=2500`, request: `firstChunkDelayMs=100, chunkIntervalMs=1000,
stallAfterChunk=2, stallMs=30000`.

Measured (nanosecond-precision, logged relative to request start):

```
deadline watchdog firing at +2505ms                              <- fired exactly on schedule
blocking read unblocked via SocketTimeoutException at +3616ms    <- ~1.1s LATER
  (lifecycle.isTerminal()=true — the watchdog had already won the CAS)
```

**Finding, measured not assumed (this is exactly what Unit 1's ADR asked Unit 2 to verify rather
than guess):** the shared deadline watchdog fires exactly on time and calls
`HttpURLConnection.disconnect()` + `Future.cancel(true)` (interrupt) on the blocked worker — but
neither actually interrupts a `BufferedReader.readLine()` call already blocked inside a native
socket read on this JDK (Azul Zulu 8.96.0.205, macOS ARM64 native). The worker thread only
unblocks ~1.1s later when *that specific read's own* socket timeout (clamped once, at connect
time, to the remaining budget at that moment — here ≈2500ms — and re-armed fresh for every
individual `read()` call) expires on its own.

A follow-up spike (`ReadTimeoutMidStreamSpike.java`, `readtimeout-midstream-spike-result.txt`)
checked whether re-clamping `setReadTimeout()` immediately before each subsequent read (to shrink
the window once the true remaining budget is smaller) would close this gap. It does not: calling
`setReadTimeout()` again after the connection is already open and a read is in flight has **no
effect on a read already blocked, nor even on the next read** in this run (shrunk to 800ms
mid-stream, the read still hadn't returned after 7 more seconds when the process was killed) —
confirming the JDK's own javadoc caveat ("must be called before the connection is established")
is not just a formality here.

**Consequence for P3-A's absolute-deadline guarantee:** the mechanism that actually bounds
user-visible latency for an in-flight blocked read is the read's own once-set, remaining-budget-
clamped socket timeout — not the watchdog's `disconnect()`/`interrupt()`. Because that clamp is
fixed at connect time and re-armed fresh for every subsequent read, a stall that begins late in the
deadline window can push the user-visible termination out to roughly
`(time the stalled read started) + (clamped timeout value)`, which can exceed
`CHAT_TOTAL_TIMEOUT_MS` by up to roughly the clamp value in the worst case (observed here: 2.5s
deadline, 3.6s actual — a 44% overrun, deliberately provoked via the stall knob to make this
worst case visible). This is a genuine, structural limitation of a blocking `HttpURLConnection`
client, not a bug introduced by this implementation — no fix is available within pure
`java.net.HttpURLConnection` (confirmed above). The three-layer defense from
`docs/decisions/phase3-timeout-cancellation.md` §4 (clamp + per-line recheck + watchdog) still
correctly bounds every *other* part of the lifecycle (admission, connect phase, the gaps between
reads) — this finding is specifically about time already spent blocked inside one `read()` call.
Recorded as a known risk for Unit 5/Formal risk assessment and as a concrete, measured data point
for Experiment A (P3-A vs P3-B) — non-blocking I/O does not have this failure mode.

Postflight after B2: `outbound_blocking_executor_active=0`, `gateway_active_streams=0`, etc. — the
request *does* eventually terminate cleanly and all resources are recovered, just later than the
configured deadline in this adversarial case. **PASS with a documented risk**, not a silent gap.

## Smoke C — admission

`smokeC-*`. `CHAT_ADMISSION_LIMIT=1`, first request holds the sole permit for ~4.2s
(`chunkCount=6, chunkIntervalMs=800`), second request fired 0.5s later.

Second request: `HTTP 503`, body `{"status":"REJECTED","reason":"executor_saturated"}`, returned in
**6.7ms** (`TIME_TOTAL:0.006745s`) — confirms `tryAcquire()` is genuinely non-blocking/immediate,
not a short wait. First request completes normally afterward.
`gateway_admission_rejected_total=1`, `gateway_requests_total{outcome="rejected"}=1`,
`gateway_requests_total{outcome="completed"}=1`, all postflight gauges 0. **PASS.**

## Client disconnect (extra manual check beyond Unit 2's minimum bar)

`disconnect-check-*`. `curl --max-time 0.6` against a 20-chunk/500ms-interval stream. Two detection
paths fired for the same request, as designed: `PrintWriter.checkError()` (in the write channel)
and the container's own `AsyncListener.onError` ("Broken pipe") — only the first to run the CAS in
`RequestLifecycle.tryTerminate()` actually performed cleanup (`servlet-write-3` won here); the
second was a no-op, confirming exactly-once terminal semantics under this real race, not just in
the concurrent unit tests. `gateway_requests_total{outcome="client_disconnect"}=1`, postflight
gauges reached 0 (full cleanup took ~1.5s from disconnect in this run — plausible given the
now-established finding above that `HttpURLConnection.disconnect()` does not immediately unblock
an in-flight blocked read on this JDK; the blocking worker's own read had to time out/error on its
own before `outbound_blocking_executor_active` could return to 0). A benign Spring MVC log warning
(`HttpMessageNotWritableException` trying to render a default error body for the already-broken
connection) appeared alongside this — cosmetic log noise, not a functional defect (the terminal
outcome was still recorded correctly, exactly once).
