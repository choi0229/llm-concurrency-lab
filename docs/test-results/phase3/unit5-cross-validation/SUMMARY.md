# Unit 5 — Phase 3 Functional Cross-Validation Summary

Status: PASS. Not a benchmark — no performance claim is made anywhere in this Unit's evidence.
Date: 2026-08-22

Produced by `scripts/run-phase3-functional-cross-validation.sh all` (with
`scripts/phase3-prometheus-diff.py` for Counter-delta/Gauge-postflight accounting). One fresh
Gateway JVM per (implementation × scenario) pair, `MOCK_LLM_BASE_URL=http://127.0.0.1:8000`
throughout (Unit 5 §9-1 DNS policy), P3-A/B/C never run concurrently.

## 0. Wording correction (Unit 4 documents)

"Two real bugs" / "framework-integration bugs" language in `gateway-webflux/README.md` and
`docs/test-results/phase3/unit4-p3c-functional/SUMMARY.md` was corrected to "a handler-model/API
integration mismatch found by functional testing, corrected by switching to WebFlux's native
`RouterFunction` model" — not a claimed Spring Framework defect. No re-execution required; this was
a wording-only correction.

## 1. Lifecycle Start Parity Audit — real gap found and fixed

Confirmed by reading actual source (not assumed): P3-A/B's `@RequestBody String body` is resolved
by Spring MVC's argument-resolver machinery *before* the handler method body runs, so body decode
always precedes `startNanos`/`requests_started`/admission/`deadlineNanos` there. P3-C's original
code computed all of those *before* subscribing to the (lazy, async) `request.bodyToMono(...)` —
backwards relative to P3-A/B. **Fixed**: moved the entire lifecycle-start sequence inside P3-C's
`flatMap(requestBody -> ...)`, so it only runs once body decode has actually completed — see
`gateway-webflux/.../ChatController.java`'s javadoc on `stream()` for the full before/after
account. Re-verified: 25 unit tests pass, Normal SSE / Admission / Absolute-deadline smokes all
re-run and pass (`docs/test-results/phase3/unit4-p3c-functional/unit5-lifecycle-parity-regression/`).

## 2. Request population — final definition (frozen)

> **T0 (application lifecycle start) := request body decode succeeded.**
> **Application request population := requests that reached this T0.**

A request whose body never finishes decoding is excluded from `gateway_requests_started_total` in
all three implementations, structurally (P3-A/B never enter the handler method; P3-C never reaches
the `flatMap`). Recorded in `docs/test-plan/phase3-design.md` §5-1.

## 3. F1–F5 results (detail: `contract-matrix.md`)

| | P3-A | P3-B | P3-C |
|---|---|---|---|
| F1 Normal | 200, 6/6 events, `completed`Δ=1 | same | same |
| F2 Timeout | `timeout`Δ=1 only | same | same |
| F3 Admission | 503 + identical JSON, `rejected`Δ=1 | same | same (Content-Type header differs, not part of frozen contract) |
| F4 Disconnect | `client_disconnect`Δ=1 only | same | same |
| F5 Upstream failure | **HTTP 200, empty body**, `upstream_error`Δ=1 | same | **HTTP 500, error JSON**, `upstream_error`Δ=1 |

Server terminal-outcome contract holds identically across all five scenarios in all three
implementations. F5's HTTP-response-shape difference is real, expected, and specifically the kind
of thing Unit 5 asked to be recorded rather than hidden (Servlet early-commit vs. reactive
lazy-commit) — not treated as a defect, not "fixed."

## 4. Scenario counter-delta accounting — held in all 15 runs

Every (config × scenario) run: `Δ(gateway_requests_started_total) == Σ Δ(gateway_requests_total{outcome=*})`,
and the correct single outcome bucket moved by exactly 1 (or exactly 2 for F3's two-request
scenario). No cross-scenario counter pollution (fresh JVM per run makes this trivially true here,
but the before/after delta methodology itself — implemented in `phase3-prometheus-diff.py` — is
what Unit 6+ will need once scenarios start sharing a longer-lived process).

## 5. SSE semantic parity

Confirmed identical parsed event sequence/payload (`delta×5` then `final`) across all three in F1.
Only difference: WebFlux's native SSE encoder omits the space after the colon
(`event:delta` vs. `event: delta`) — SSE-spec-equivalent, not a contract violation (Unit 1's
client-observable contract only requires identical event sequence/payload, not byte-identical
framing).

## 6. Common metric parity (detail: `metrics-parity.md`)

All common `gateway.*` metrics confirmed present under identical names/label semantics in all
three, from actual `/actuator/prometheus` output. Two (`bytes_relayed`,
`first_chunk_relay`/`stream_duration`) have a confirmed physical-boundary difference — see below.

## 7. Server TTFC/stream-duration comparability — NOT comparable across implementations

Confirmed by reading the actual call sites (not assumed): P3-A/B record `first_chunk_relay` from
inside `PerStreamWriteChannel.doWrite()`, *after* `PrintWriter.write()+flush()` actually executes;
P3-C records it from the response Flux's `.doOnNext()`, *before* WebFlux's own SSE
encoder/Reactor Netty channel write. Structurally different boundaries — not made to match (would
require adding an artificial write-buffering layer to P3-C or changing P3-A/B's lifecycle, both
forbidden by Unit 5 §18/§28).

## 8. Client-side latency declared authoritative (frozen)

**Decision, recorded in `docs/decisions/phase3-metrics-contract.md` §8-1**: client-side k6
measurement (TTFC, total stream duration) is the Formal authoritative latency source for
cross-implementation comparison. `gateway_first_chunk_relay_seconds`/`gateway_stream_duration_seconds`
are implementation diagnostics only from here forward — never used to rank P3-A/B/C against each
other.

## 9. Implementation-specific metric matrix

Detail: `metrics-parity.md`. Headline: P3-A has `outbound_blocking_executor_*` that P3-B/C don't;
P3-A/B have `servlet_write_*`/`tomcat_*` that P3-C doesn't; P3-B/C have `reactor_netty_connection_provider_*`/
`reactor_netty_http_client_*` that P3-A doesn't; **no implementation has `reactor_netty_http_server_*`**
(Boot's default embedded WebFlux server doesn't self-instrument at that level — confirmed absent,
not assumed, re-confirmed this Unit). No metric is faked as an always-zero placeholder for a
resource an implementation doesn't have.

## 10. Postflight

All 15 (config × scenario) runs: `gateway_active_streams`/`gateway_admission_active`/
`gateway_upstream_active` = 0, plus whichever implementation-specific gauges apply
(`reactor_netty_connection_provider_pending_connections` = 0 for P3-B/C;
`servlet_write_executor_active`/`_queue_depth`/`_buffered_frames` = 0 and
`outbound_blocking_executor_active` = 0 for P3-A/B where applicable). See each scenario's
`accounting.txt`.

## 11. Architecture parity (detail: `architecture-parity.md`)

Static cross-check (import-statement-level, not naive full-text grep — P3-C's javadoc comments
*mention* `HttpURLConnection` for comparison purposes, which a naive grep would misreport) +
startup logs + live thread-name evidence, all consistent with the intended architecture: P3-A has
`HttpURLConnection`+blocking executor+Servlet+write executor and no WebClient; P3-B has
Servlet+write executor+WebClient and no `HttpURLConnection`; P3-C has WebClient+Reactor Netty
server and none of the Servlet-side machinery.

## 12. P3-B/P3-C outbound resource parity

`WebClientConfig.java` confirmed **byte-identical** (`diff` output empty) between P3-B and P3-C —
the Unit 4-0 decision is still in effect, unmodified.

## 13. Correctness issue found and fixed this Unit

Only one: the lifecycle-start ordering gap in P3-C (§1 above). No other correctness bug found in
cross-validation. No tuning applied to any implementation to make results look more similar than
they structurally are (F5's HTTP-status difference and the `first_chunk_relay`/`bytes_relayed`
boundary differences were left as-is and documented, per Unit 5 §28's explicit prohibition on that
kind of "fix").

## 14. Warm-up requirement — frozen as a principle

Recorded in `docs/test-plan/phase3-design.md` §9-3: all future Screening/Formal measurement must
apply the same warm-up phase to all three implementations before measuring; warm-up itself is
excluded from the measurement population. No warm-up performance numbers compared in this Unit.

## 15. Config axes to keep identical through Unit 6 (frozen list, not values)

Recorded in `docs/test-plan/phase3-design.md` §9-2: `CHAT_ADMISSION_LIMIT`,
`CHAT_TOTAL_TIMEOUT_MS`, `CHAT_ASYNC_WATCHDOG_MARGIN_MS` (P3-A/B), `MOCK_LLM_BASE_URL`
(127.0.0.1 fixed), the `WEBCLIENT_*` connection-pool/loop-resources axes (P3-B/C),
`SERVLET_WRITE_*` (P3-A/B), `CHAT_BLOCKING_POOL_SIZE` (P3-A). Exact numeric values remain
unfrozen until Unit 6 screening produces a basis for them.
