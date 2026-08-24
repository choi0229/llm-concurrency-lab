# Unit 5 — Client Functional Contract Matrix (F1–F5)

Status: Unit 5 functional cross-validation (not Formal/load testing)
Date: 2026-08-22

Produced by `scripts/run-phase3-functional-cross-validation.sh all`, one fresh Gateway JVM per
(implementation × scenario) pair, `MOCK_LLM_BASE_URL=http://127.0.0.1:8000` (Unit 5 §9-1 DNS
policy) throughout. Raw evidence (curl output, Prometheus before/after snapshots, accounting) is
under `A/`, `B/`, `C/` in this directory, one subdirectory per scenario.

| Scenario | P3-A | P3-B | P3-C | Parity |
|---|---|---|---|---|
| **F1 Normal SSE** | `200`, 6 events (5×delta+final), `completed`Δ=1 | `200`, 6 events, `completed`Δ=1 | `200`, 6 events, `completed`Δ=1 | **PASS** — identical event sequence/payload; only cosmetic `event: `→`event:` spacing differs (SSE-spec-equivalent, Unit 4 §4) |
| **F2 Absolute timeout** | `200`, empty-after-2-events body, `timeout`Δ=1, `client_disconnect`Δ=0, `upstream_error`Δ=0, `completed`Δ=0 | same shape, `timeout`Δ=1, others 0 | same shape, `timeout`Δ=1, others 0 | **PASS** — outcome exactly `timeout` in all three, no double-counting into `client_disconnect` |
| **F3 Admission** | req1 `200 completed`, req2 `503` immediate reject, `rejected`Δ=1 | same | same | **PASS** — status/body identical; `Content-Type` header differs (see below, not part of the frozen contract) |
| **F4 Client disconnect** | `client_disconnect`Δ=1, `timeout`Δ=0, `upstream_error`Δ=0 | same | same | **PASS** |
| **F5 Upstream failure** | `200`, empty body, `upstream_error`Δ=1 | `200`, empty body, `upstream_error`Δ=1 | **`500`**, Boot default error JSON body, `upstream_error`Δ=1 | **Server outcome PASS, client-visible HTTP response differs** — see below, not a defect |

"Parity" judges the *server terminal outcome contract* (Unit 1's frozen 7-value set) and the
*parsed SSE semantics* — not byte-identical wire framing or HTTP status/headers on every path.

## F1 — Normal SSE (detail)

All three: `HTTP 200`, `Content-Type: text/event-stream;charset=UTF-8` (P3-C: same value, header
casing differs only because Reactor Netty's server lowercases some header names — not evaluated
further, HTTP header names are case-insensitive per RFC 7230). Parsed event sequence identical:
`delta(seq=1..5)` then `final`. First event arrives before the stream ends (no full buffering) in
all three (`TIME_STARTTRANSFER` < `TIME_TOTAL` in every raw curl summary). Counter delta:
`gateway_requests_started_total`=1, `gateway_requests_total{outcome="completed"}`=1, all other
outcome deltas 0, in all three.

## F2 — Absolute timeout (detail)

Identical stall scenario to Units 2–4 (`stallAfterChunk=2, stallMs=30000`,
`CHAT_TOTAL_TIMEOUT_MS=2500`). All three: client total time ≈2.5s (P3-A 3.61s in this specific
harness run — see note below), server outcome exactly `timeout`. Counter delta:
`gateway_requests_total{outcome="timeout"}`=1, `{outcome="client_disconnect"}`=0,
`{outcome="upstream_error"}`=0, `{outcome="completed"}`=0 — no double-counting from the internal
Reactor `CANCEL`(P3-B/C) that timeout triggers internally.

**Note on P3-A's F2 time in this specific harness run (3.61s vs. the 2.5s deadline):** this
reproduces the same finding as Unit 2 Smoke B — `HttpURLConnection.disconnect()` does not promptly
interrupt an in-flight blocked `readLine()`; the worker only unblocks when that read's own
once-clamped socket timeout naturally expires. This is not new evidence, not a regression, and not
a performance claim — it is the same architectural characteristic already documented in Unit 2,
now reproduced under the common harness for completeness of the cross-validation record.

## F3 — Admission (detail)

`CHAT_ADMISSION_LIMIT=1`. Second (concurrent) request rejected in all three: `HTTP 503`, body
`{"status":"REJECTED","reason":"executor_saturated"}` — byte-identical body and status in all
three. **Difference found (documented, not fixed — see `docs/decisions/
phase3-metrics-contract.md` §8-4):** `Content-Type` header is `text/event-stream;charset=UTF-8` for
P3-A/B (inherited from the response object's content type being set before the admission check —
unchanged Phase 1/2 behavior) vs. `application/json` for P3-C (explicitly set in the reject
branch). Neither the frozen contract (Unit 1) nor Unit 5 requires this header to match — only
status and body were frozen.

Also found — **and corrected in Unit 5.5 §0** (not left as a documented-only difference, unlike the
Content-Type note above): the diagnostic `gateway_upstream_cancel_total` counter incremented for
the rejected request in P3-A/B (it fired for any non-`completed` terminal outcome, including
`rejected`, in those two modules' shared `RequestLifecycle.tryTerminate()`) but did not increment
in P3-C (P3-C's reject path returns directly, before any `RequestLifecycle` is ever constructed —
there is no `tryTerminate()` call to fire it from). P3-C's behavior was the semantically correct
one (no upstream call was ever attempted for a rejected request), so P3-A/B's metric-bookkeeping
condition was corrected to match — see `docs/test-results/phase3/unit5.5-diagnostics/
upstream-cancel-fix-regression/SUMMARY.md`. All three now agree: `upstream_cancel`Δ=0 for this
scenario.

`reactor_netty_connection_provider_pending_connections` = 0 throughout for P3-B/C (confirms the
connection pool never became a second, hidden admission limiter).

## F4 — Client disconnect (detail)

`curl --max-time 0.6` cutoff after a warmup request. All three: outcome `client_disconnect`
exactly once, postflight gauges 0. Cancellation reached Mock LLM within ~2ms of the actual
disconnect in P3-B/C (Reactor cancellation propagation, per Unit 3/4 findings); P3-A's
Servlet-side detection (`checkError()`/`AsyncListener.onError`) resolves the outcome the same way,
architecturally slower to fully release resources per Unit 2's finding but still exactly-once and
correct.

## F5 — Upstream connection failure (detail — the most significant Unit 5 finding)

`MOCK_LLM_BASE_URL` pointed at a verified-unbound local port (connection refused, no retry/backoff
variable, deterministic). All three record server outcome `upstream_error` exactly once
(`gateway_requests_total{outcome="upstream_error"}` Δ=1, all other outcome deltas 0) —
**the server accounting contract holds identically.**

**But the client-visible HTTP response differs, and this is architectural, not a bug (docs/
decisions/phase3-metrics-contract.md §8-3):**

```
P3-A: HTTP 200, empty body           (response already committed before the connect failure —
P3-B: HTTP 200, empty body            Servlet model: status/headers set right after admission,
                                       before the outbound call is even attempted)

P3-C: HTTP 500, Boot default error   (WebFlux defers response commit until the body Publisher's
      JSON body                       first signal is known; the connection-refused error arrives
                                       before any commit, so it becomes a real HTTP error response)
```

Recorded as a genuine, expected consequence of the two response models being compared
(Experiment B) — not corrected to make the three look alike (Unit 5 §12/§28 explicitly forbid
that).
