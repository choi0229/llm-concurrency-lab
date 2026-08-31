# Phase 4 Open Unit 7.4 — Client Connection Lifecycle / Ephemeral-Port Control Forensics

Pure offline audit + source-code forensics. **No new load run. No sysctl changes. No Open
Screening/Formal.** All analysis below uses existing artifacts from
`docs/test-results/phase4/unit7.2-r160-frontdoor-isolation/m2-r160-probe-uncapped/` (the
`maxConnections=50000` probe) plus direct reads of the pinned `xk6-sse@v0.1.11` Go source
(`.native-runtime/go/pkg/mod/github.com/phymbert/xk6-sse@v0.1.11/sse.go`) and the
`scripts/sample-client-socket-telemetry.sh` sampler script.

## 1. Socket sampler double-count: CONFIRMED

`sample-client-socket-telemetry.sh`'s filter (`grep -E "\.${TARGET_PORT}[[:space:]]"`) matches
`.18102` in **either** the Local Address or Foreign Address column of `netstat -n -p tcp` output,
without distinguishing which. On loopback, every logical TCP connection produces **two** rows in the
same host's connection table: the client's socket (local=ephemeral, foreign=18102) and the Gateway's
accepted socket (local=18102, foreign=ephemeral) — both real, both counted, no double-listing bug in
`netstat` itself, but the filter treats both as "matching."

**Verified across the full time series** (145 timestamp-matched samples from `m2-r160-probe-uncapped`,
not just one lucky instant): `established / tomcat_connections_current_connections` ratio =
min 1.953, max 2.266 (early ramp-up noise), **median 2.014, mean 2.028** — converging tightly to
almost exactly 2.0 once steady-state is reached. This is definitive: the "established" count is
double-counting client+server rows per connection, not measuring distinct client sockets.

## 2. Corrected client-side ESTABLISHED peak

**≈10,593** (raw peak 21186 / 2), which matches Tomcat's own independently-reported peak (10549)
almost exactly — the ~0.4% residual difference is fully explained by sampling-instant skew between
the 1-second netstat poll and the 1-second Prometheus scrape, not a separate error source. This
number, not 21186, is the correct estimate of distinct client-side sockets/ports in ESTABLISHED
state at peak.

## 3. Corrected TIME_WAIT peak

**Not a clean halving — most likely predominantly server-side, not client-port-consuming at all.**
Unlike ESTABLISHED, a TIME_WAIT row is *not* symmetric: only the side that sends the first FIN
(the "active closer") lingers in TIME_WAIT; the passive-close side's socket is removed from the table
once it reaches CLOSED. Per §9-10 below, the *server* (Tomcat, enforcing its own `keepAliveTimeout`)
is the far more likely active closer for the bulk of these connections, since the client
(`xk6-sse`) never sends an explicit close and Go's `http.Transport` does not proactively close idle
pooled connections either. If that holds, the observed `time_wait` peak (3037, raw, uncorrected —
no independent cross-check metric exists to verify a specific factor the way `tomcat_connections_
current_connections` did for ESTABLISHED) is largely Tomcat-side TIME_WAIT bookkeeping, which does
**not** block the client from allocating new local ports (TIME_WAIT restricts *the active closer's*
4-tuple reuse, not the passive side's port availability). This is disclosed as a well-reasoned
inference from the connection-lifecycle evidence (§8-10), not a value independently re-derived from
raw per-connection data (which the sampler did not preserve — it only wrote aggregate counts, not
raw `netstat` rows, so an exact endpoint-column recount is not reconstructable after the fact; this
is a genuine, disclosed limitation, not filled in with an assumption).

## 4. Corrected unique ephemeral-port occupancy

**Best estimate ≈10,593** (the corrected ESTABLISHED figure), not the previously-reported
"established(21186)+time_wait(3037)=24223." This is **below** the 16384-port ephemeral range — the
Unit 7.3 conclusion that the steady-state population alone exceeds the range is **withdrawn**. No
exact "unique local source port" recount was possible (raw per-row netstat text was not preserved,
only aggregate state counts) — this is the best available correction, clearly labeled as an estimate,
not an exact recount.

## 5. EADDRNOTAVAIL semantics

`dial tcp ...: connect: can't assign requested address` is Go's `net` package surfacing the OS-level
`EADDRNOTAVAIL` errno from the `bind()`/`connect()` syscall path when the kernel cannot find an
available local (source) address/port to originate the outbound connection from. This is exactly the
signature of local ephemeral-port-space pressure at the moment of the attempt — confirmed against
Go's standard `net` package error-wrapping behavior (a `*net.OpError` wrapping a syscall `errno`,
which `dial tcp` messages surface verbatim) and consistent with the near-instant failure time (median
1ms elapsed — a local resource-allocation failure, not a network round trip). No other plausible
cause fits an instant, local, address-allocation-specific error string this precisely; not otherwise
disputed.

## 6. Tomcat organic connection peak

**≈10,549** (Tomcat's own gauge, `maxConnections=50000` probe, confirmed non-binding at that
ceiling). Simple diagnostic ratio `10549 / 160 req/s ≈ 65.9s` is recorded as an observation, **not**
asserted as "the connection lifetime is 65.9s" (Little's Law division doesn't directly yield a
lifetime without assuming a specific queueing model) — it is flagged as a strong anomaly relative to
the nominal `8s stream + 20s keepAliveTimeout ≈ 28s` expectation, roughly 2.4x higher, requiring
the explanation in §8-10.

## 7. Implied connection residency observation

The 65.9s figure is 2.4x the nominal 28s per-connection budget. Two contributing, non-exclusive
mechanisms are identified (§8-10, §6-note): (a) the confirmed `xk6-sse` per-iteration Transport
defect means **every** completed connection is retained, idle and un-reused, in its throwaway
per-iteration Transport's idle pool, requiring the server's own timeout-driven cleanup rather than
being recycled quickly by request reuse; (b) under a connector
managing ~10,000+ simultaneous idle-or-active connections, Tomcat's own periodic background
timeout-sweep (the mechanism that actually enforces `keepAliveTimeout`) may take measurably longer
per full sweep cycle than the nominal 20s value assumes, since it must iterate the full connection
set each cycle — this is a plausible, mechanism-consistent explanation for *why* residency exceeds
the nominal budget, not independently instrumented/proven here (Tomcat exposes no per-sweep-cycle
timing metric).

## 8. Actual Tomcat runtime keepalive

Not independently re-confirmed via live JMX/actuator read in this Unit (no new load was run, and no
existing artifact captured a JMX/env-endpoint dump of these specific properties — only the `_config_`
Prometheus gauges for `maxConnections`/`threads.max` exist in any prior artifact, and Micrometer's
TomcatMetrics binder does not expose `keepAliveTimeout`/`connectionTimeout`/`maxKeepAliveRequests` as
metrics at all). Unit 7.3's `javap`-based source-derived values (`keepAliveTimeout` unset → falls
back to `connectionTimeout` = `SocketProperties.soTimeout` = 20000ms; `maxKeepAliveRequests=100`) are
**reused, not re-litigated** — these are hardcoded numeric defaults in a version-pinned dependency
(Spring Boot 4.1.0 / Tomcat 11.0.22), not environment-mutable runtime state, so a source read is a
reliable, if not live-runtime-confirmed, source of truth. Live confirmation would require adding a
read-only diagnostic (e.g., temporarily exposing `management.endpoint.env`) and a new load run —
flagged as a possible future step, not performed here per this Unit's "no new load" constraint.

## 9. xk6-sse v0.1.11 HTTP transport construction

Read directly from `sse.go` lines 197-207 (pinned source, not assumed):

```go
sseClient.httpClient = &http.Client{
    Timeout: args.timeout,
    Transport: &http.Transport{
        DialContext:     state.Dialer.DialContext,
        Proxy:           http.ProxyFromEnvironment,
        TLSClientConfig: tlsConfig,
        // FIXME phymbert: it would be more interesting to allow reusing the transport across iterations
        DisableKeepAlives: state.Options.NoConnectionReuse.ValueOrZero() || state.Options.NoVUConnectionReuse.ValueOrZero(),
    },
}
```

**A brand-new `http.Client` + `http.Transport` is constructed on every single `sse.open()` call** —
i.e., every k6 iteration. The comment on the `DisableKeepAlives` line is the extension **author's own
acknowledged FIXME**: reusing the transport across iterations was recognized as desirable and never
implemented. `DisableKeepAlives` itself evaluates to `false` in our setup (neither
`--no-connection-reuse` nor `--no-vu-connection-reuse` k6 CLI flags are used by any harness script in
this project), so keep-alive IS enabled at the Go transport level — but since the Transport itself is
thrown away after one request, that keep-alive capability is never actually exercised for its
intended purpose (reusing a pooled connection for a *second* request through the *same* Transport).

## 10. Response-body close lifecycle / connection reuse

`Client.closeResponseBody()` (called automatically on normal SSE-stream EOF, `sse.go` lines 288-302)
calls **only** `c.resp.Body.Close()`. It does **not** call `c.httpClient.CloseIdleConnections()`.
That cleanup exists **only** in the separate `Client.Close()` method (lines 271-276), which the
*calling JS script* must invoke explicitly (`client.close()`). **None of this project's workload
scripts (`03`/`04`/`05`/`06`) ever call `client.close()`** in their normal completion path (confirmed
by direct re-read of `05-phase4-open-arrival-rate.js`/`06-phase4-open-diagnostic-r160.js` — the only
place in the whole repo that calls `.close()` is `sse-verification-test.js`, an intentional
mid-stream-disconnect test unrelated to Screening/Formal workloads).

Consequence: `resp.Body.Close()` on a body drained cleanly to EOF is the normal signal Go's
`http.Transport` uses to return a connection to its **idle pool** for reuse — it does **not** send a
TCP FIN or otherwise tear down the socket. Since the owning `Transport` is a one-shot object never
used for a second request, that pooled idle connection is never reused, and — critically — the
`Transport` was constructed with no explicit `IdleConnTimeout` (Go's `http.Transport{}` zero value
disables the idle-timeout reaper for that field), so nothing on the **client side** will ever
proactively close it. The connection remains open, idle, forgotten, until the **server** (Tomcat)
unilaterally closes it once its own `keepAliveTimeout` (20s, §8) elapses.

**This is the confirmed, source-verified root mechanism**: every completed SSE request leaves behind
one idle, un-reused, un-reaped TCP connection that only the server's own timeout eventually reclaims
— multiplying the effective concurrent-connection population far beyond what an "N req/s × ~8s
active-stream" estimate would predict.

## 11. VU-to-connection relationship

In the `maxConnections=50000` probe: `vus.max = 1278` (k6's own peak concurrent-VU usage) vs. Tomcat
`tomcat_connections_current_connections` peak = **10,549** — a ratio of **≈8.25 connections per
peak-concurrent VU**. **"1 VU = 1 persistent connection" does not hold at all** — confirms §9-10's
mechanism directly: each VU cycles through many iterations, and each iteration's now-abandoned
connection lingers well after that VU has already moved on to (and opened a fresh connection for) its
next iteration, so connections accumulate as a function of *iteration throughput over the lingering
window*, not concurrent VU count.

## 12. Why ~10,549 connections accumulated

Consistent, mechanism-explained chain: (a) `xk6-sse` never reuses a Transport/connection across
iterations (§9, source-confirmed) and never explicitly closes idle connections after normal
completion (§10, source-confirmed); (b) the resulting retained idle connections are reclaimed only by
the **server's** `keepAliveTimeout` (confirmed 20s nominal, §8), whose *effective* enforcement latency
under a connector managing ~10k connections may exceed the nominal value (§7, plausible, not
independently measured); (c) at a sustained 160 iterations/s, each leaving one retained idle
connection open for a multi-second-to-tens-of-seconds window before server-side reclamation, the steady-state
population settles at whatever `arrival_rate × effective_total_residency` implies — empirically
≈10,549 at R=160, i.e., an effective per-connection residency of ≈66s (§6), well above the naive ≈28s
estimate, plausibly for the reason in §7.

## 13. Workaround candidates comparison

| Candidate | Fixes root cause? | Changes measurement semantics? | Cross-model fair? | Repo-scoped? | Host-wide impact? | Comparability w/ prior Closed/Open results | Reproducibility | Implementation difficulty |
|---|---|---|---|---|---|---|---|---|
| **A. Fix xk6-sse's per-iteration Transport (upstream/vendor patch)** | Yes, most directly | No (transport-level only) | Yes (all models use same k6 binary) | Yes, but requires rebuilding the pinned custom k6 binary (`load-test-k6/Dockerfile`) with a patched/forked extension | None | High — no workload-visible change | High (deterministic once built) | High (Go patch + custom xk6 build + revalidation of the whole SSE pipeline, e.g. Unit 4-4's verification) |
| **B. Call `client.close()` explicitly after stream completion in the workload scripts** | Yes, directly targets the confirmed gap (§10) | Minimal — same HTTP semantics, just releases the idle pool client-side instead of leaving it to the server's timeout | Yes | **Yes, trivially — one line in the shared completion path of `03`/`04`/`05`(fork)/`06`** | None | Needs re-validation that TTFC/duration/outcome metrics are unaffected (should be, since `.close()` only runs after the outcome is already determined) | High | Low |
| **C. Multiple loopback source addresses (127.0.0.x)** | No — doesn't reduce connection *count*, only spreads the same count across more address space | No | Yes | Depends — `xk6-sse`'s `sse.open()` takes no per-call `LocalAddr`/bind-address option (confirmed: `sseOpenArgs` in `sse.go` has no such field; `DialContext: state.Dialer.DialContext` is k6's own shared dialer, not exposed for per-request override from JS) — **not supported without an xk6-sse code change**, i.e., collapses into Candidate A's difficulty | N/A | Would need `lo0` alias IPs configured (a host-level, if minor, change) | Unclear until implemented | Low once built | Medium-high (needs the same source change as A, plus host aliasing) |
| **D. Separate loadgen host** | Removes the shared-host confound entirely | Introduces a **new** confound (real network path vs. loopback) | Yes | No — infrastructure change | Requires a second machine | **Breaks comparability** with every existing Closed/Open result, all captured on loopback on one host — would need a new environment epoch | Depends on new infra | High (new environment ADR, re-baseline everything) |
| **E. Recalibrate SAFE_OPEN_MAX down for this host** | No — masks the symptom, doesn't fix the defect | No workload semantic change, but changes the frozen scalability-test range | Yes | Yes | None | Preserves comparability of *lower* rates; loses ability to test near the original 256 ceiling | High | Low (a calibration re-run) |
| **F. sysctl (ephemeral range / TIME_WAIT)** | Partially — raises the ceiling the defect bumps into, doesn't fix the defect | No | Yes | No — host-wide | Yes, affects the whole machine, all processes | Unclear/reversible if restored, but changes the control-host state under every future run unless explicitly reset each time | Depends on OS restart persistence | Low technically, high in host-scope-of-effect |

**Assessment**: given §1-4's correction (the true steady-state client-port occupancy, ~10,593, does
not actually exceed the 16384 range), and §9-12's clear, source-confirmed root cause, **Candidate B is
the most directly evidenced, lowest-risk, repo-scoped fix** — it addresses the confirmed defect
without touching Gateway source, host OS state, or the pinned k6/xk6-sse binary. Candidate A is the
"more correct" long-term fix (fixing the actual upstream defect) but is materially higher effort for
what is, for this project's purposes, an equivalent outcome to B. Neither is executed in this Unit
(§19's "자동 실행 금지").

## 14. sysctl 필요성

**Not established as necessary.** The corrected occupancy estimate (§4, ~10,593) is below the current
16384-port range. The genuine EADDRNOTAVAIL failures observed are better explained by the connection-
lifecycle defect (§9-12) inflating population far past nominal expectations, plus possible transient/
sub-second bursts not captured by 1-second sampling, than by a hard, confirmed exhaustion of the
nominal range at its stated size. No sysctl change is recommended before trying a repo-scoped fix
(Candidate B) first.

## 15. SAFE_OPEN_MAX 하향 필요성

**Not established as necessary at this time**, for the same reason — the dominant, confirmed cause is
a loadgen-side software defect with a low-risk repo-scoped fix candidate (B) not yet tried, not an
inherent host or architecture ceiling. Recalibrating `SAFE_OPEN_MAX` down would be treating a fixable
loadgen artifact as if it were a fundamental control-range limit.

## 16. Case A/B/C/D/E verdict

# Case A — LOADGEN CONNECTION-LIFECYCLE ISSUE, repo-scoped fix possible (PRIMARY finding)

Confirmed via direct pinned-source read (`xk6-sse@v0.1.11`, §9-10): every `sse.open()` call
constructs a fresh, never-reused `http.Transport`; the workload's normal completion path never calls
`client.close()`; the resulting idle connections are reclaimed only by the server's `keepAliveTimeout`
(confirmed 20s). This fully and directly explains the ≈8.25 connections-per-VU accumulation (§11) and
the ≈2.4x-above-nominal organic connection population (§6-7, §12).

A secondary correction is also confirmed and folded into this verdict, not treated as a separate
Case: the **client-side telemetry itself materially overstated the severity** of the resulting
port pressure (§1-4, a confirmed sampler double-counting bug plus a likely TIME_WAIT
misattribution) — the true steady-state occupancy (~10,593) does not clearly exceed the ephemeral
range, which weakens (without fully eliminating — genuine EADDRNOTAVAIL errors did occur) a "Case B:
confirmed host limit" reading on its own.

**This is not classified as Case D (composite)**: with the telemetry correction applied, there is no
longer a second, independently-necessary host-limit component to combine with — the loadgen defect
alone is sufficient to explain both the elevated connection population and, plausibly, transient
brushes against the (larger-than-previously-believed) available port headroom.

## 17. Recommended next action

**A. Repo-scoped connection-lifecycle fix → regression → final epoch.**

Concretely: add an explicit `client.close()` call to the shared SSE-workload completion path once an
iteration's outcome is determined (Candidate B, §13) in the Open scenario files, matching this
project's existing "fork, don't modify frozen files" precedent for any file already covered by a
freeze. This is **not executed in this Unit** per the explicit "Connection: close 실험은 아직 금지" /
"자동 실행 금지" instructions — flagged as the recommended next step for explicit authorization, not
carried out here.

## 18. Open Screening resume readiness

**FAIL** (unchanged from Unit 7.3) — the connector-ceiling question is now much better understood
(root cause identified with high confidence, telemetry correction applied, a low-risk fix candidate
identified) but the fix itself has not been implemented, tested, or regression-validated. No sysctl
change, no SAFE_OPEN_MAX change, no new canonical epoch.
