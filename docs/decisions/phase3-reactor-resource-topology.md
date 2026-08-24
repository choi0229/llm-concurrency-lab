# ADR: Phase 3 Reactor Netty Resource Topology (P3-B / P3-C outbound client)

Status: Accepted (Unit 4-0 scope)
Date: 2026-08-22

This is a capability/static audit for Experiment B's variable control, not a benchmark — no load
was run, no performance claim is made.

## 1. Question

Does P3-B's current `WebClientConfig` (`gateway-mvc-webclient`) use Reactor Netty's global default
event-loop resources, or an explicit dedicated pool? And when P3-C adds a Reactor Netty **server**
(WebFlux) into the same JVM as an outbound WebClient, do the two end up sharing event-loop threads
by default?

## 2. Audit method

Not answered from memory or javadoc alone — verified by direct execution at two levels.

### 2-1. Raw Reactor Netty (no Spring)

`ResourceTopologySpike.java` (`docs/test-results/phase3/unit4-p3c-functional/
unit4-0-resource-topology-audit/`): an `HttpServer.create()` (no `.runOn(...)`) bound locally, and
an `HttpClient.create()` (no `.runOn(...)`) firing 20 requests at it, both in the same JVM. Result:

```
total distinct reactor-http-nio-* threads alive in JVM: 10
availableProcessors: 10
```

10, not 20. If server and client had independent default pools, each sized to
`availableProcessors()` (Reactor Netty's own default sizing), the JVM would show 20 distinct
threads. It shows exactly 10 — **the server and client are drawing from the same global pool**
(`reactor.netty.http.HttpResources`, the singleton both `HttpServer.create()` and
`HttpClient.create()` default to when neither specifies `.runOn(LoopResources)`).

### 2-2. Actual Spring Boot 2.7.18 WebFlux app

`SpikeWebfluxTopologyApplication.java` (same directory): a real Boot 2.7.18
`spring-boot-starter-webflux` app (Boot's auto-configured embedded `NettyWebServer`) with a
manually-built `WebClient` constructed **exactly like `gateway-mvc-webclient`'s
`WebClientConfig`** (`WebClient.builder().clientConnector(new
ReactorClientHttpConnector(HttpClient.create()))`, no `.runOn(...)`). After 15 requests through a
`/probe` endpoint that calls back into the same app's own `/server-echo` endpoint:

```
allNioReactorThreadsInJvm=[reactor-http-nio-1..10]   (10 total)
availableProcessors=10
```

Same result at the application level: **10, not 20.** Confirmed — this is not a raw-Reactor-Netty
quirk that Boot's auto-configuration papers over; it reproduces with the actual dependency versions
and construction pattern Phase 3 uses.

## 3. Decision — Option B: explicit shared client `LoopResources`

Per the brief's stated preference, and because it keeps Experiment B's controlled variable clean:
**P3-B's and P3-C's outbound WebClient share one explicit, dedicated `LoopResources` instance**,
separate from whatever event-loop pool P3-C's own Reactor Netty *server* uses (left on Boot's
default for the server — the server pool has no P3-B counterpart to stay symmetric with, so there
is nothing to control for there).

```java
LoopResources loopResources = LoopResources.create("gateway-webclient-loop", workerCount, daemon);
HttpClient httpClient = HttpClient.create(connectionProvider)
        .runOn(loopResources, false)   // false = do not prefer native (epoll/kqueue) — this repo's
                                        // dependency set has no macOS-native transport artifact on
                                        // the classpath (Unit 1/2/3 spikes already observed the
                                        // equivalent DNS-resolver-native-macos warning for the same
                                        // reason), so native would silently fall back to NIO anyway;
                                        // requesting NIO explicitly is just being honest about it.
        ...
```

`workerCount` functional default: `Runtime.getRuntime().availableProcessors()` (matches what
Reactor Netty's own global default would have sized itself to anyway — this change is about
**ownership/isolation**, not about picking a different number), overridable via
`WEBCLIENT_LOOP_THREADS` (functional default, not Formal-frozen, same policy as every other pool
size in Phase 3).

Why not Option A (accept the sharing): the brief's own reasoning is adopted as-is — accepting the
default sharing would mean any P3-B vs P3-C comparison of outbound behavior is partly confounded by
"P3-C's inbound Front traffic and outbound Mock LLM traffic compete for the same worker threads,
P3-B's don't (Tomcat handles Front, a *shared-with-nothing-else* pool handles Mock LLM calls)" —
an accidental, un-intended difference layered on top of the deliberate one (Servlet vs reactive
server model). Explicit separation removes that confound so the only intended difference stays
"Tomcat request model + Servlet write executor" (P3-B) vs "Reactor Netty server + reactive
response" (P3-C).

## 4. P3-B change required — minimal, justified here

`gateway-mvc-webclient/src/main/java/.../webclient/WebClientConfig.java` gets one new `@Bean`
(`webClientLoopResources`) and one added `.runOn(...)` call on the existing `HttpClient` bean. No
other change — API, admission, lifecycle, write-path, and outcome semantics are all untouched.
Unit 3's full functional smoke suite (A/B/C/D) and unit test suite were re-run after this change
(`docs/test-results/phase3/unit3-p3b-functional/unit4-0-regression/`) to confirm no regression.

## 5. P3-C's own server resources

P3-C's Reactor Netty server is left on Spring Boot's default embedded-server configuration (no
custom `ReactorResourceFactory`/`.runOn(...)` override) — it is free to use the global
`HttpResources` pool for its own accept/worker loops. Since the outbound WebClient in both P3-B and
P3-C is now pinned to the separate `gateway-webclient-loop` pool, this no longer collides with
anything on the client side. P3-C's server-side event-loop thread names/count are recorded as a
diagnostic in Unit 4's functional smoke evidence, not treated as an invariant to control.

## 6. DNS native resolver warning (Unit 3 §18 / this Unit's §18)

Observed in every Unit 1-3 run: `Unable to load
io.netty.resolver.dns.macos.MacOSDnsServerAddressStreamProvider... fallback to system defaults`.
Root cause (confirmed by dependency inspection, not guessed): `netty-resolver-dns-native-macos` is
on the classpath (a transitive dependency of `reactor-netty-http`), but the actual native library
artifact it needs is platform-classified — Gradle only resolved the generic Java-API jar, not a
`-osx-aarch_64`/`-osx-x86_64` native classifier jar, so at runtime Netty falls back to the JDK's
built-in DNS resolution path. This does not affect functional correctness (Mock LLM resolution
still succeeds, as every prior Smoke test's successful requests already show) and adding the
missing native classifier dependency is not necessary for Functional or Formal — per this Unit's
own §18 policy, Formal traffic targets `MOCK_LLM_BASE_URL=http://127.0.0.1:8000` (an IP literal),
which requires no DNS resolution at all, removing the warning's relevance entirely rather than
fixing the library gap. `docs/test-results/phase3/unit4-p3c-functional/` confirms `127.0.0.1`
works end-to-end for P3-C's functional smoke.
