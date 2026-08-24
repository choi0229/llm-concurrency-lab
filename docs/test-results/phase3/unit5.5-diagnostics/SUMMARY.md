# Unit 5.5 — Blocking / Slow-Client Diagnostic Summary

Status: Diagnostic only — not a benchmark, no performance ranking. PASS.
Date: 2026-08-22

## §0 — gateway_upstream_cancel_total semantic cleanup

Fixed before starting D1/D2 (see `docs/test-results/phase3/unit5.5-diagnostics/
upstream-cancel-fix-regression/SUMMARY.md` for full detail). One-line condition fix in P3-A/B's
`RequestLifecycle.tryTerminate()` — `rejected` no longer increments `gateway_upstream_cancel_total`
(no upstream call is ever attempted for a rejected request). P3-C already had this semantic by
construction. F3 re-verified for all three: `rejected`Δ=1, `upstream_cancel`Δ=0 in all three.

## D1 — BlockHound

**Version/compatibility research** (official sources: GitHub README, `docs/quick_start.md`,
Maven Central listing — not guessed): `io.projectreactor.tools:blockhound:1.0.17.RELEASE`.
`reactor-core` ≥3.3.0 (we have 3.4.34) has built-in SPI integration. Only documented JVM-specific
caveat anywhere in the official docs is JDK13+ needing `-XX:+AllowRedefinitionToAddDeleteMethods`
(native-method redefinition restriction introduced in JDK13) — Java 8 predates this restriction
and needs no special flag.

**Positive control** (`docs/test-results/phase3/unit5.5-diagnostics/blockhound/positive-control/`):
standalone project (`scripts/diagnostic-tools/blockhound-positive-control/`), pinned to the exact
same reactor-core 3.4.34, run on the exact same Zulu 8 JVM. `Thread.sleep()` on
`Schedulers.parallel()` → `BlockingOperationError` thrown as expected, no extra JVM flags needed.
**PASS.**

**P3-B** (`.../blockhound/p3b-normal/`): real Spring context, real WebClient/`ConnectionProvider`/
`LoopResources`, one Normal-SSE request against real Mock LLM. `BLOCKHOUND_RESULT=
NO_VIOLATION_OBSERVED`. Stated precisely: *"the tested P3-B Reactor path (WebClient receive →
bounded-queue offer) showed no BlockHound-observable blocking operation in this run"* — not
generalized further.

**P3-C** (`.../blockhound/p3c-normal/`): same setup. **First run found a real violation**:
`BlockingOperationError: Blocking call! java.io.FileInputStream#readBytes`, full stack trace
tracing through `UUID.randomUUID() → SecureRandom.nextBytes() → sun.security.provider.NativePRNG →
FileInputStream.read()` (Java 8's default `SecureRandom` reads `/dev/urandom` synchronously on
every call), originating at `ChatController.java:112` — the `requestId` generation the Unit 5 §1
lifecycle-parity fix moved inside the reactive `flatMap`, now executing on a
`reactor-http-nio-*` thread. Deterministic, on every request — not classloading noise. P3-A/B have
the identical `UUID.randomUUID()` call but on a Servlet thread, which BlockHound doesn't mark
non-blocking, so it was never flagged there and was not touched.

**Fix**: replaced `UUID.randomUUID()` with a `ThreadLocalRandom`-based UUID construction
(`fastRandomRequestId()`) in `gateway-webflux`'s `ChatController` only — `requestId` is a
log-correlation identifier, not a security/uniqueness-critical value. Re-verified: 25/25 unit tests
pass, BlockHound re-run clean (`BLOCKHOUND_RESULT=NO_VIOLATION_OBSERVED`, same request still
receives all 6 events correctly).

## D2 — Slow Client

**Tool capability audit** (`.../slow-client/capability-audit.md`): confirmed by reading the actual
`xk6-sse@v0.1.11` Go source (not docs) that sleeping in the JS `event` callback genuinely stalls
the underlying TCP socket read (unbuffered channel handoff between the reader goroutine and the
single JS-callback-running control loop) — no separate custom tool needed. One tool-usage bug found
and fixed in the diagnostic script itself (not any Gateway): the `error` handler must call
`client.close()` or the control loop hangs forever after the server aborts the connection.

**Workload**: Mock LLM's own existing knobs (`chunkCount`, `chunkSizeBytes`, `chunkIntervalMs=0`)
— no new Mock feature added. Two scenarios, same for both P3-B and P3-C: **overflow** (60×64KB
chunks, deliberately larger than P3-A/B's 32-frame buffer capacity) and **sustained** (20×64KB,
within capacity). P3-A was not tested (Unit 5.5 §11 — H3-f is specifically MVC+WebClient vs.
WebFlux, P3-A is out of this diagnostic's scope).

### P3-B result

**Overflow scenario**: `write_overflow` fired within ~270ms of request start — the bounded
per-stream buffer (capacity 32, functional default) filled and the fail-fast mechanism engaged
exactly as designed (`docs/decisions/phase3-mvc-webclient-write-path.md` §6). **A real bug was
found and fixed here**: the overflow-discard branch in `PerStreamWriteChannel.offer()` cleared the
buffer without decrementing `servlet_write_stream_buffered_frames` — the gauge was left
permanently overcounted (32, then 64 after a second overflowing stream), violating the postflight
invariant. Fixed identically in P3-A and P3-B (byte-identical bug, byte-identical fix), a
regression assertion added to the existing overflow unit test, re-verified end-to-end (gauge
correctly returns to 0.0 after the fix).

**Sustained scenario** (20 chunks, within capacity): `servlet_write_executor_active` observed at
`1` for part of the request (the write executor thread genuinely occupied), `servlet_write_stream_buffered_frames`
peaked at 8/32, and — the clearest single number — `servlet_write_duration_seconds_max` =
**0.605s** for this run (vs. ~1-4ms in a normal-speed Smoke A run). Concrete, measured evidence
that a slow client's TCP-level backpressure propagates all the way into a blocking
`PrintWriter.write()+flush()` call taking over half a second, with the bounded buffer providing
runway (not unbounded growth) before any fail-fast would trigger.

### P3-C result — the headline finding of this Unit

**Both scenarios**: no overflow (there is no such buffer). Overflow-equivalent workload (60×64KB,
3.9MB total) delivered completely and correctly to the client — client-observed duration **9.32s**.
Sustained workload (20×64KB): client-observed duration **3.18s**. Reactor's demand-based
backpressure did let the whole payload through rather than failing fast — consistent with "no
application-level unbounded buffer" in the sense Unit 4/5 already established.

**But — found by directly comparing gateway log timestamps against the client-observed duration,
not assumed**: in the 60-chunk run, `terminal: outcome=completed` (and therefore admission-permit
release, `gateway_admission_active`/`gateway_upstream_active` decrement) fired **169ms** after
request start — while the client took **9.32 seconds** to actually finish receiving the data. The
20-chunk run reproduces the same pattern at smaller scale: terminal at **25ms**, client duration
**3.18s**. This is not a measurement artifact of the already-known `first_chunk_relay`/
`stream_duration` boundary difference (Unit 5 §7/§8) — it is the **admission permit itself** being
released while megabytes are still physically draining to a slow client.

**What was actually measured, stated precisely**: P3-C's reactive response Publisher terminal
boundary (the point where `doOnComplete`/`tryTerminate("completed")` fires) precedes client
physical receive completion by a large, reproducible margin. **What was not measured**: this run
did not instrument Reactor Netty's internal channel write/flush pipeline directly, so "the response
Flux is considered complete once every item has been accepted into Reactor Netty's own internal
channel write buffer, gated by Netty's writability/high-water-mark rather than confirmed delivery"
is a plausible **structural explanation** consistent with WebFlux/Reactor Netty's documented
demand-driven, asynchronous-write design — not a directly-instrumented, confirmed root cause. It is
recorded here as a hypothesis, not a fact.

**This is not a bug** — whatever the precise internal mechanism, the observed behavior (terminal
preceding physical delivery) is a consequence of the reactive/non-blocking model itself (Unit 4 §5
explicitly forbids adding an artificial write-buffering/executor layer to P3-C to "fix" this, which
would be the only way to make permit release wait for confirmed delivery, and doing so would remove
the reactive model's own advantage that this experiment exists to measure). Recorded as a genuine,
measured **capacity-planning consideration** — and, as of Unit 6's Admission Semantics Audit, as
a real open design question about what `CHAT_ADMISSION_LIMIT` should mean across P3-A/B/C, not a
closed matter: P3-C's admission ceiling bounds how many requests are *administratively* in flight,
not how many megabytes may be sitting in Reactor Netty's internal write buffers for slow clients at
any given moment — a dimension P3-A/B's bounded per-stream buffer + fail-fast overflow does
explicitly bound (at the cost of failing those slow-client streams outright, per the overflow
finding above). Neither "P3-C leaks" nor "P3-B is safer" is asserted — the trade-off itself is the
finding, resolved by the Admission Semantics Audit before Unit 6 Screening begins (see
`docs/decisions/phase3-admission-semantics-unification.md`).

## Correctness bugs found and fixed this Unit

1. `gateway_upstream_cancel_total` counting `rejected` (§0) — P3-A/B only.
2. `UUID.randomUUID()` causing a real BlockHound-caught blocking call on a Reactor thread — P3-C
   only.
3. `servlet_write_stream_buffered_frames` gauge leak on buffer overflow — P3-A/B only (identical
   bug, identical fix).

All three: minimal, targeted, metric/bookkeeping-level fixes. No admission, lifecycle, timeout,
write-path *architecture*, executor, WebClient, or P3-C server-model change. All re-verified by
unit tests plus a live re-run of the diagnostic that originally found each issue.

## Phase 3 design document update (Unit 5.5 §18)

`docs/test-plan/phase3-design.md` updated: BlockHound stays OFF for Screening/Formal
(instrumentation overhead); Slow Client workload is not part of Screening/Formal traffic. The
P3-C admission-permit-vs-physical-delivery finding above is recorded as a known risk to weigh when
Unit 6+ designs Formal's client population and connection/concurrency limits.
