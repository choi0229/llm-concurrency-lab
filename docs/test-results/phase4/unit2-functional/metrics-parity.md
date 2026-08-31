# Phase 4 Unit 2 — Common Metric Semantic Parity Table

All names confirmed by actually scraping `/actuator/prometheus` on live M1/M2/M3 instances during
Unit 2 (Boot 4.1.0 / Micrometer 1.17.0 / Tomcat 11.0.22 / Reactor Netty 1.3.6) — none guessed or
carried over from Phase 3 without re-verification. Raw dumps: `m1/prometheus.txt`,
`m2/prometheus.txt`, `m3/prometheus.txt`.

## Common custom metrics (Primary — cross-model comparable)

| Concept | M1 | M2 | M3 | Semantic parity | Primary/Diagnostic |
|---|---|---|---|---|---|
| Requests started | `gateway_requests_started_total` | same | same | identical | Primary |
| Terminal outcome | `gateway_requests_total{outcome}` | same | same | identical label set (`completed`,`rejected`,`timeout`,`upstream_error`,`client_disconnect`,`internal_error`,`write_overflow`) | Primary |
| Active in-flight requests | `gateway_active_requests` | same | same | identical (includes M1 queue wait, per section 9) | Primary |
| Upstream active | `gateway_upstream_active` | same | same | identical | Primary |
| Bytes relayed | `gateway_bytes_relayed_total` | same | same | same formula, NOT exact-wire-byte-accurate for M3 (native encoder differs, Phase 3 section 8-2 caveat carried over) | Diagnostic |
| First upstream event latency | `gateway_first_upstream_event_seconds_{count,sum,max}` | same | same | identical definition | Diagnostic |
| Request duration | `gateway_request_duration_seconds_{count,sum,max}` | same | same | identical definition, but see Metrics Authority note below | Diagnostic |

**Metrics Authority reminder** (docs/decisions/phase4-metrics-contract.md section 8): the
above server-side timers are diagnostic only — Formal SLO (TTFC/stream-duration p95) is always
k6/xk6-sse client-side, never these.

## Framework-provided metrics — CONFIRMED this Unit (supersedes "candidate" status in
docs/decisions/phase4-metrics-contract.md section 4)

| Concept | M1 | M2 | M3 |
|---|---|---|---|
| JVM live platform threads | `jvm_threads_live_threads` | `jvm_threads_live_threads` | `jvm_threads_live_threads` |
| Process CPU | `process_cpu_usage` (ratio 0.0-1.0, NOT `process_cpu_seconds_total` — that Phase1/2 `simpleclient` metric does not exist under Micrometer) | same | same |
| System CPU / core count | `system_cpu_usage`, `system_cpu_count` | same | same |
| **Open FD count** (new finding — not anticipated in Unit 1) | `process_files_open_files` / `process_files_max_files` | same | same |
| Heap | `jvm_memory_used_bytes{area="heap"}` | same | same |
| GC pause | `jvm_gc_pause_seconds_{count,sum,max}` | same | same |
| Tomcat thread pool | `tomcat_threads_busy_threads`, `tomcat_threads_current_threads`, `tomcat_threads_config_max_threads` | same | **absent (no Tomcat)** |
| Tomcat connections | `tomcat_connections_current_connections`, `tomcat_connections_config_max_connections` | same | **absent** |
| Reactor Netty client connection pool | **absent (M1/M2 don't use WebClient)** | **absent** | `reactor_netty_connection_provider_{active,idle,pending,total,max}_connections` (required `.metrics(true)` on `ConnectionProvider`+`HttpClient` — NOT on by default, Unit 2 finding, added in `WebClientConfig`) |
| Reactor Netty **server** connections (never confirmed even in Phase 3) | **absent** | **absent** | `reactor_netty_http_server_connections`, `reactor_netty_http_server_connections_active` (required `.metrics(true)` via a `NettyServerCustomizer` bean — not enabled by Boot Actuator auto-config either; Unit 2 finding) |
| Generic Spring task executor (Boot's own `applicationTaskExecutor`, unrelated to Phase 4's own executors) | absent | absent | `executor_active_threads`, `executor_pool_size_threads`, `executor_queued_tasks`, etc. — present on M3 only, an artifact of WebFlux's default `TaskExecutor` auto-config, NOT a Phase 4 metric |

**No metric name in this table was assumed** — every row above was read directly out of a live
`/actuator/prometheus` scrape during this Unit. Two additions were made to Unit 2 source
specifically because the default configuration did not expose them: `ConnectionProvider.metrics
(true)` + `HttpClient.metrics(true, ...)` in `WebClientConfig`, and a `NettyServerCustomizer` bean
(`NettyServerMetricsConfig`) enabling `.metrics(true, ...)` on the embedded `HttpServer`.

## M1-specific metrics (confirmed exported)

`executor_active`, `executor_pool_size`, `executor_queue_depth`, `executor_queue_capacity`,
`executor_rejected_total`, `executor_queue_wait_seconds_{count,sum,max}`,
`executor_task_duration_seconds_{count,sum,max}` — all confirmed present and moving correctly
during F1/F5/F6 functional runs (see `m1/prometheus.txt` and the F5/F6 unit test evidence).

## M2-specific metrics (confirmed exported)

`virtual_tasks_active`, `virtual_tasks_started_total` — confirmed present, `active` returns to 0
postflight (also proven directly in `VirtualTaskSubmitterTest`).

## Servlet write-path metrics (M1/M2 common, confirmed exported)

`servlet_write_executor_active`, `servlet_write_executor_pool_size`,
`servlet_write_executor_queue_depth`, `servlet_write_stream_buffered_frames`,
`servlet_write_overflow_total{source="perstream"|"executor"}` — all confirmed present, all return
to 0 postflight in every functional scenario run this Unit.

## Postflight invariant — confirmed clean after every functional scenario (F1-F9)

```
gateway_active_requests == 0
gateway_upstream_active == 0
executor_active == 0                          (M1 only)
executor_queue_depth == 0                     (M1 only)
virtual_tasks_active == 0                     (M2 only)
servlet_write_executor_active == 0            (M1/M2)
servlet_write_executor_queue_depth == 0       (M1/M2)
servlet_write_stream_buffered_frames == 0     (M1/M2)
```
