# ADR: Phase 4 Open-model Tomcat Connector Headroom (M1/M2)

Status: Accepted (Phase 4 Open Unit 7.3 scope)

## 1. What was found (Unit 7.1 → 7.2)

Unit 7.1's initial forensics on M2 R=160 (`docs/test-results/phase4/unit7.1-m2-r160-frontdoor-
forensics/`) found a ~5400-request population gap between k6's client-side iteration count and every
Gateway-internal counter (Tomcat connector, servlet dispatch, app dispatch, Mock receipt/completion —
all in exact agreement with each other), and initially concluded "Tomcat connector limiter: ruled
out" — that conclusion was **wrong**, because it relied only on a post-hoc final Prometheus snapshot
(`tomcat_connections_current_connections=244`) taken after the load had already ended and connections
had drained.

Unit 7.2's diagnostic (`docs/test-results/phase4/unit7.2-r160-frontdoor-isolation/`) re-ran R=160
against Mock directly (clean: 0 gap, 0 SYN_SENT, 100% success) and against M2 with the connector's
own time series captured for the first time. Result: **`tomcat_connections_current_connections`
plateaued at exactly `maxConnections=8192` for 126 of 188 one-second samples (~2 minutes, essentially
the whole measurement window)**. Simultaneously: `tomcat_threads_busy_threads` peaked at only 107 of
200 (thread pool never close to its ceiling), Gateway CPU averaged ~3.3%, and every request that
*did* get a TCP connection succeeded end-to-end with zero errors. The client's own captured error
(`sse.open()`'s return value, logged verbatim, not fabricated) was a raw Go `net/http` dial failure:
`dial tcp 127.0.0.1:18102: connect: operation timed out` — a TCP connection-establishment failure,
occurring before any HTTP request is even sent.

**Conclusion: M2 R=160's failure is a Tomcat/Gateway front-door connection-admission ceiling
(`server.tomcat.max-connections`, an unexamined Spring Boot default), not a Virtual Thread execution
capacity finding.** This is confirmed by five independent, simultaneous pieces of evidence (see
Unit 7.2 `SUMMARY.md` §6) — not by the mere existence of an untuned default.

## 2. Why `maxThreads` and `acceptCount` are NOT changed

- `tomcat_threads_busy_threads` peaked at 107/200 in the R=160 diagnostic — the thread pool was never
  within striking distance of its configured ceiling. No evidence exists anywhere in this project's
  data that `maxThreads=200` binds under Open-model load.
- No metric exposes `acceptCount` directly (Micrometer's TomcatMetrics binder does not bind it), and
  no independent evidence (e.g., a distinguishable SYN-backlog-overflow signature separate from the
  `maxConnections` ceiling) points to it as a *separate* binding constraint. The observed client-side
  `SYN_SENT` accumulation (Unit 7.2, up to 1322 concurrent) is fully explained as a downstream
  consequence of Tomcat's Acceptor pausing `accept()` once `maxConnections` is reached — it does not
  require invoking `acceptCount` as a second, independent cause.
- Per this project's standing principle (never tune a parameter without direct binding evidence for
  that specific parameter), only `maxConnections` is amended here.

## 3. Confirmed Tomcat/Spring Boot effective defaults (source-derived, not memory)

Verified directly from the actual dependency jars used by this build (`javap` disassembly of the
constructor/getter bytecode — priority-1 "runtime/JMX metric" was not practical to obtain for
non-metric static config without adding new instrumentation, so priority-2 "exact embedded source"
was used, per the governing instruction's stated priority order):

| Setting | Value | Source |
|---|---|---|
| `server.tomcat.max-connections` | 8192 | `TomcatServerProperties` constructor (`spring-boot-tomcat-4.1.0.jar`), confirmed matching the live `tomcat_connections_config_max_connections` metric in every run this project has captured |
| `server.tomcat.accept-count` | 100 | `TomcatServerProperties` constructor, same jar |
| `server.tomcat.threads.max` | 200 | confirmed live via `tomcat_threads_config_max_threads` in every run |
| `server.tomcat.max-keep-alive-requests` | 100 | `TomcatServerProperties` constructor, same jar |
| `server.tomcat.keep-alive-timeout` | **not set** by Spring Boot (field left `null` in `TomcatServerProperties`) → Tomcat's own `AbstractEndpoint.getKeepAliveTimeout()` falls back to `getConnectionTimeout()` when unset (confirmed via `javap` of `tomcat-embed-core-11.0.22.jar`) | — |
| `server.tomcat.connection-timeout` → effective `SocketProperties.soTimeout` | **20000 ms (20s)** | `SocketProperties` constructor (`tomcat-embed-core-11.0.22.jar`) — `sipush 20000` immediately followed by the `soTimeout` field assignment. **This corrects Unit 7.2's speculative "~60s" keep-alive estimate**, which was explicitly flagged there as not yet confirmed. |

Neither `gateway-phase4-platform-queue` (M1) nor `gateway-phase4-virtual-thread` (M2)'s
`application.yml` overrides any of these (both files are otherwise identical apart from
`server.port`), and neither has a `TomcatConnectorCustomizer`/`TomcatProtocolHandlerCustomizer` bean
— confirmed by grep of both modules' source trees, not assumed from one module and extrapolated to
the other.

## 4. Formula

Effective per-connection lifetime budget for `maxConnections` accounting purposes = time spent
actively streaming (bounded by the frozen stream-duration SLO) **plus** the time Tomcat will hold an
otherwise-idle connection open afterward waiting for a next request that never comes
(`keepAliveTimeout`, confirmed = 20s, §3):

```
EXPECTED_CONNECTION_LIFETIME_BUDGET_S = STREAM_DURATION_SLO_S (10.0) + KEEPALIVE_TIMEOUT_S (20.0)
                                      = 30.0

OPEN_TOMCAT_CONNECTION_BUDGET = SAFE_OPEN_MAX (256) * EXPECTED_CONNECTION_LIFETIME_BUDGET_S (30.0)
                              = 7680

OPEN_TOMCAT_MAX_CONNECTIONS = ceil(OPEN_TOMCAT_CONNECTION_BUDGET * HEADROOM (1.25))
                            = ceil(9600.0)
                            = 9600
```

This is the same `target_max_load * worst_case_holding_time * 1.25` shape already used for M3's Open
`WEBCLIENT_MAX_CONNECTIONS = ceil(256 * 10.0 * 1.25) = 3200`
(`docs/test-plan/phase4-open-screening-protocol.md` §2.2) — the only difference is M1/M2's connector
must additionally budget for Tomcat's post-response keep-alive hold time (confirmed 20s), which
Reactor Netty's WebClient pool (a purely outbound client-side pool, unrelated to Tomcat's inbound
keep-alive semantics) does not need to.

**Final value: `OPEN_TOMCAT_MAX_CONNECTIONS = 9600`**, applied identically to M1 and M2 via
`SERVER_TOMCAT_MAX_CONNECTIONS=9600` (Spring Boot relaxed-binding environment variable — verified
working with no source-code change: `tomcat_connections_config_max_connections` reads back exactly
9600 when this env var is set). `server.tomcat.threads.max` and `server.tomcat.accept-count` are left
at their defaults (§2).

## 5. Why M1 and M2 both, not just M2

Both `gateway-phase4-platform-queue` and `gateway-phase4-virtual-thread` are Spring MVC / embedded
Tomcat applications sharing byte-identical Tomcat-relevant configuration (§3). Amending only M2's
`maxConnections` while leaving M1 on the untuned default would make any M1-vs-M2 Open-model
comparison confounded by a config difference neither model's own architecture chose — cross-model
fairness requires the same connector headroom policy for both. M3 (`gateway-phase4-webflux`) uses
Reactor Netty, not Tomcat, and is unaffected by this ADR; its own Open pool sizing
(`OPEN_M3_MAX_CONNECTIONS=3200`) was already frozen in Unit 7 and is unchanged (see
`docs/test-plan/phase4-open-screening-protocol.md` §2.2 — no conflict, confirmed still in effect,
not re-tuned here per the governing instruction's explicit "결과 보고 재튜닝 금지").

## 6. Host safety cross-check

`maxConnections` is an **admission ceiling enforced by a counting gate** (Tomcat's `LimitLatch`), not
a pre-allocation — setting it to 9600 does not itself allocate 9600 threads, sockets, or buffers
upfront; it only raises how many *may* be concurrently open before the connector's Acceptor pauses.
Actual usage under the frozen Open rate range (SAFE_OPEN_MAX=256) is expected to stay near
Little's-Law levels (~256 × 8s nominal ≈ 2000, well below 9600) except under genuine overload, which
is exactly the condition this headroom exists to stop from being an artificial ceiling.

- Gateway FD ceiling: `process_files_max_files` observed at 1,048,576 in every prior run — 9600
  connections (each consuming a handful of FDs at most) is far below any risk of exhaustion.
- Memory: Tomcat NIO per-connection buffer overhead is on the order of tens of KB; 9600 connections
  worst-case is on the order of a few hundred MB, well within normal JVM heap/off-heap budgets for
  this lab host — not independently benchmarked here, flagged as an estimate, not a hard measurement.
- Client-side ephemeral port space (16384, confirmed via `sysctl net.inet.ip.portrange`,
  `docs/test-results/phase4/unit7.2-r160-frontdoor-isolation/`) is a *separate* budget from the
  server's `maxConnections` and is addressed by the VU-sizing revision
  (`docs/decisions/phase4-open-loadgen-vu-sizing.md`), not by this ADR.

## 7. Regression result: 9600 is INSUFFICIENT — a second, host-level ceiling exists

The §4 formula's regression check (Unit 7.3, `m2-r160-regression1`) **failed**:
`tomcat_connections_current_connections` still plateaued at exactly the new ceiling (9600.0, 110/188
samples ≥99% of it), and 2680 `zero_event_terminal` failures recurred with the identical
`dial tcp ...: connect: operation timed out` signature — reduced from the original ~4096 (at
`maxConnections=8192`) but not eliminated. **§4's formula (`STREAM_DURATION_SLO + keepAliveTimeout`)
under-estimates the true effective connection-lifetime budget; 20s of confirmed keep-alive alone
does not fully explain the observed pressure.**

A follow-up probe (not a candidate final value -- a deliberately oversized, clearly-non-binding
`SERVER_TOMCAT_MAX_CONNECTIONS=50000`, in the same spirit as this project's existing
control-calibration methodology: measure the true unconstrained behavior first, freeze a headroomed
value after) found:

- `tomcat_connections_current_connections` peaked at **10549** (`tomcat_connections_non_binding=True`
  confirmed) — Tomcat's own side is genuinely non-binding at this ceiling.
- Client-side `syn_sent` dropped to ~0 (peak 1) — confirms the earlier `SYN_SENT` accumulation
  (Unit 7.2 finding) was entirely a downstream artifact of the `maxConnections` gate, not a separate
  `acceptCount`/kernel-backlog constraint; §2's decision to leave `acceptCount` unchanged is
  reaffirmed, not undermined.
- **But 1129 `zero_event_terminal` failures still occurred, with a completely different signature:
  `dial tcp 127.0.0.1:18102: connect: can't assign requested address`** (macOS `EADDRNOTAVAIL`),
  failing near-instantly (median 1ms elapsed, not a multi-second timeout). This is the textbook
  signature of **client-host ephemeral-port exhaustion**, not a Gateway-side limit at all.
- Client-side socket telemetry in that same probe: `established` peaked at **21186**,
  `time_wait` peaked at **3037** — combined (~24223) **exceeds the host's ephemeral port range
  (16384, confirmed via `sysctl net.inet.ip.portrange`)**.

  **Correction (Unit 7.4, `docs/test-results/phase4/unit7.4-client-connection-lifecycle-forensics/
  SUMMARY.md` §1-4): this raw arithmetic is superseded, not deleted.** The `established` figure was
  confirmed (via the full time series, not one instant: median ratio 2.014 against Tomcat's own
  `tomcat_connections_current_connections` gauge across 145 matched samples) to be **double-counted**
  by the socket sampler's port filter, which matches `.18102` in either the local or foreign address
  column — on loopback, every logical connection produces two rows (the client's socket and the
  Gateway's accepted socket), both counted. The corrected client-side ESTABLISHED peak is
  **≈10,593** (≈21186/2), matching Tomcat's own peak (10549) almost exactly. `time_wait` is likely
  predominantly server-side bookkeeping, not client-port-consuming (Unit 7.4 §3, §9-10: the server,
  not the client, is the connection's active closer). **The corrected steady-state client-side
  occupancy (~10,593) does not clearly exceed the 16384-port ephemeral range** — the original
  "host ephemeral-port limit confirmed" reading here is weakened. The genuine `EADDRNOTAVAIL`
  failures observed are still real (not a counting artifact) and are better explained by Unit 7.4's
  confirmed loadgen connection-lifecycle root cause than by a hard, confirmed exhaustion of the
  nominal port range.

**Conclusion: there are two independent, layered ceilings, not one.** Raising `maxConnections` far
enough removes the Gateway-side one, but exposes a second, host-level one (the loadgen client's own
ephemeral port supply, further pressured by TIME_WAIT accumulation from R=160's high connection
churn). Comparing against Unit 7.2's Direct-Mock control at the identical rate (`established` peak
only 4168, `time_wait` peak only 5137 there) shows the Gateway/Tomcat connection *dwell time* as seen
by the client is substantially longer when routed through M2 than when talking to Mock directly —
consistent with (though not fully isolated to) Tomcat keep-alive-holding behavior, but evidently
larger in effect than the confirmed 20s keep-alive value alone accounts for.

**This second ceiling is a host OS setting (`net.inet.ip.portrange.first/last` and/or `net.inet.tcp.
msl`, the TIME_WAIT-governing parameter), not a project file.** Changing it is a host-wide, not a
repo-scoped, action, and was not authorized by the governing instructions for this Unit (which
explicitly forbade "새 sysctl 값 변경" during the diagnostic phase, and this amendment phase did not
separately authorize it either). **No sysctl change has been made.**

## 8. RESOLVED (Unit 7.4 → 7.5): the real root cause was a loadgen connection-lifecycle defect, not a Gateway/host sizing problem

Unit 7.4 found the client-side socket telemetry underlying §7's "second ceiling" conclusion was
itself confirmed double-counted (client+server loopback row pairs both matched by the sampler's
naive filter — verified across the full time series, median ratio 2.014 against Tomcat's own gauge);
the corrected client occupancy (~10,593) did not clearly exceed the ephemeral range after all. Unit
7.4 then read `xk6-sse@v0.1.11`'s pinned source directly and found the true cause: every `sse.open()`
call constructs a brand-new, never-reused `http.Transport` (the extension author's own acknowledged
`// FIXME`), and this project's workload scripts never called `client.close()` — so every completed
request retained its idle, un-reused per-iteration Transport connection, unclosed on the client side,
until only the *server's* `keepAliveTimeout` eventually reclaimed it.

Unit 7.5 fixed this at the loadgen/workload level (`load-test-k6/scenarios/05-phase4-open-arrival-
rate.js`, `06-phase4-open-diagnostic-r160.js`): capture the `client` reference from the setup
callback and call `.close()` immediately after `sse.open()` returns (safe and exactly-once, since
`sse.open()` only returns once the stream already reached a terminal state). Re-measured at R=160:
Tomcat's organic connection population dropped from ~10,549 (broken) to **~1276–1278** — matching
Little's Law on stream duration *alone* (`160 × ~8s ≈ 1280`), confirming post-stream keep-alive
retention is now negligible. Zero dial-timeouts, zero `EADDRNOTAVAIL`, zero dropped iterations,
100% population match end-to-end, in three independent regression runs
(`m2-r160-lifecyclefix1`, `m2-r160-finalvalue1`, plus the `direct-mock-r160-lifecyclefix1` control).

**Final formula (supersedes §4's `9600`):**

```
OPEN_TOMCAT_MAX_CONNECTIONS = ceil(SAFE_OPEN_MAX(256) * STREAM_DURATION_SLO_S(10.0) * 1.25) = 3200
```

The `keepAliveTimeout` term from §4 is no longer needed now that the client properly closes idle
connections instead of retaining them unclosed — this is the *same* formula shape already used for M3's
`OPEN_M3_MAX_CONNECTIONS=3200` (`docs/test-plan/phase4-open-screening-protocol.md` §2.2), now
converging to the identical value for M1/M2 too. Validated non-binding at this exact final value
(`tomcat_connections_non_binding=True`, peak 1278/3200, `m2-r160-finalvalue1`) — not extrapolated
from a single probe's peak, but derived from the protocol-level understanding that per-connection
residency is now dominated by stream duration alone. `maxThreads`/`acceptCount` remain unchanged
(§2; reaffirmed by Unit 7.4's finding that `SYN_SENT` collapsed to near-zero once the true
connection-count pressure was addressed, with no independent evidence either parameter binds).

**`OPEN_TOMCAT_MAX_CONNECTIONS=3200`, applied identically to M1 and M2, is the value now in
`scripts/run-phase4-open-benchmark.sh`.** See `docs/test-results/phase4/unit7.5-client-lifecycle-fix/
SUMMARY.md` for the full regression evidence.

## 8. Not post-hoc result-driven tuning

This amendment corrects a Gateway configuration gap that was never deliberately chosen (an unexamined
default), discovered through direct, reproducible, multi-source forensic evidence (Unit 7.1 → 7.2),
before any Open Screening result is treated as final — it is a **control-range headroom correction**,
the same category of fix as the Unit 6.5 extended Closed-control-range work and the Unit 7's own M3
WebClient pool re-freeze for the Open range, not a retroactive adjustment made because a result was
disliked. The previously-recorded `m2-r160-screen1` classification is reclassified per
`docs/decisions/phase4-scalability-definition.md`'s CONTROL/CONFIG-vs-model distinction (see Unit 7.3
`SUMMARY.md` §2), not deleted or silently overwritten.
