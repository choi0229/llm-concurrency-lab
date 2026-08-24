# Unit 5 — Static Architecture Cross-check & Outbound Resource Parity

Status: Unit 5 functional cross-validation
Date: 2026-08-22

Method: import-statement grep (not full-text grep — javadoc comments in P3-C reference
`HttpURLConnection` by name for comparison purposes, which a naive full-text grep would
misreport as usage; confirmed those are comments only, not imports) + dependency
declarations (`build.gradle`) + startup logs + live `jstack` thread-name evidence
(Units 0–4, re-confirmed here).

## Static architecture table (Unit 5 §23)

| | P3-A | P3-B | P3-C |
|---|---|---|---|
| `HttpURLConnection` import | **yes** (`upstream/MockLlmClient.java`) | no | no |
| Blocking outbound executor | **yes** (`ExecutorConfig.blockingOutboundExecutor`, `ThreadPoolExecutor`) | no | no |
| Servlet API import (`javax.servlet.*`) | **yes** (`ChatController`, `RequestLifecycle`) | **yes** (same two files) | no |
| Servlet write executor / `PerStreamWriteChannel` | **yes** | **yes** (byte-identical class, 1-line divergence — Unit 3) | no |
| `WebClient` import | no | **yes** (`upstream/MockLlmClient.java`, `webclient/WebClientConfig.java`) | **yes** (same two files) |
| Reactor Netty **server** | no (Tomcat) | no (Tomcat) | **yes** (`spring-boot-starter-webflux`, `NettyWebServer`) |
| `spring-boot-starter-web` (Tomcat) dependency | **yes** | **yes** | no |
| `spring-boot-starter-webflux` dependency | no | **yes** (WebClient only) | **yes** (server + WebClient) |

Startup log confirmation (unchanged since Units 2–4, re-verified during this Unit's harness runs):
P3-A/B log `Tomcat started on port(s): ...`, never a Netty server line; P3-C logs `Netty started on
port ...`, never Tomcat.

## Outbound WebClient resource parity — P3-B vs P3-C (Unit 5 §24)

Confirms the Unit 4-0 decision (`docs/decisions/phase3-reactor-resource-topology.md`) is still in
effect, verified by diffing the actual `WebClientConfig.java` files:

```
$ diff gateway-mvc-webclient/.../webclient/WebClientConfig.java gateway-webflux/.../webclient/WebClientConfig.java
(no output -- byte-identical)
```

| Axis | P3-B | P3-C | Same? |
|---|---|---|---|
| `ConnectionProvider` builder name | `gateway-webclient-pool` | `gateway-webclient-pool` | yes |
| `maxConnections` | `WEBCLIENT_MAX_CONNECTIONS` env, default = admission ceiling | same | yes |
| `pendingAcquireMaxCount` | `WEBCLIENT_PENDING_ACQUIRE_MAX_COUNT` env, default 1 | same | yes |
| `pendingAcquireTimeout` | `WEBCLIENT_PENDING_ACQUIRE_TIMEOUT_MS` env, default 1000ms | same | yes |
| Connect timeout | `WEBCLIENT_CONNECT_TIMEOUT_MS` env, default 3000ms | same | yes |
| Response timeout (low-level safety, not the deadline) | `WEBCLIENT_RESPONSE_TIMEOUT_MS` env, default 120000ms | same | yes |
| `LoopResources` | explicit `gateway-webclient-loop`, `WEBCLIENT_LOOP_THREADS` env (default = CPU count) | same | yes |
| `MOCK_LLM_BASE_URL` | `http://127.0.0.1:8000` (Unit 5 §9-1) | same | yes |

**Live thread-name evidence, this Unit's harness runs:** `jstack` during F1 for both P3-B and
P3-C shows `gateway-webclient-loop-nio-N` threads for the outbound client, with **zero name
overlap** against either P3-B's Tomcat `http-nio-*`/`servlet-write-*` threads or P3-C's own
`reactor-http-nio-*` server threads. Stated as "live thread names observed during this functional
smoke run," not generalized to a fixed "pool size" — the actual count depends on
`WEBCLIENT_LOOP_THREADS`'s functional default (`Runtime.availableProcessors()`), which is a
runtime-environment-dependent number, not a configured constant to quote as if it were fixed
(Unit 5 §2's explicit caution).

**P3-C's own server `LoopResources`** is intentionally a separate, uncontrolled resource (no P3-B
counterpart to keep parity with) — left on Spring Boot's default embedded-server configuration, as
decided in Unit 4-0 §5.
