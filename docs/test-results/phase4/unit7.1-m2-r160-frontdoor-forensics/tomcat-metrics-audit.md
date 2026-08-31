# M2 R=160 (screen1) — Tomcat Effective Config + Live Metrics Audit

All values below are read directly from `gateway-metrics-final.txt` /
`gateway-metrics-before-wave.txt` in the M2 R=160 screen1 artifact — actual runtime metric values,
not documentation defaults recalled from memory. `management.metrics.enable...` is not set in
`application.yml`; the `tomcat_*` family is exposed because `server.tomcat.mbeanregistry.enabled:
true` is set, which lets Micrometer's TomcatMetrics binder read the connector's JMX MBeans.

## Effective config (confirmed from runtime metrics, not assumed)

| Setting | Runtime value | Source |
|---|---|---|
| `maxConnections` | **8192** | `tomcat_connections_config_max_connections{name="http-nio-18102"}` |
| `maxThreads` | **200** | `tomcat_threads_config_max_threads{name="http-nio-18102"}` |
| `acceptCount` | not exposed as a metric by Micrometer's TomcatMetrics binder (no `tomcat_*` series for it) — could not be read from runtime metrics; Spring Boot's documented default is 100, unconfirmed here since `application.yml` sets neither this nor any Tomcat connector customizer. |

Neither `application.yml` nor any Java source in `gateway-phase4-virtual-thread/src/main/java/`
overrides `server.tomcat.threads.*` / `server.tomcat.accept-count` / `server.tomcat.max-connections`,
and no `TomcatConnectorCustomizer`/`TomcatProtocolHandlerCustomizer` bean exists in this module —
confirmed by grep, not assumed.

## Live metric values at final scrape (00:26:42, immediately after k6 exited, before Gateway shutdown)

| Metric | Value | Ceiling | Headroom |
|---|---|---|---|
| `tomcat_connections_current_connections` | 244.0 | 8192 | 97% free |
| `tomcat_threads_current_threads` | 200.0 | 200 (pool had grown to its max) | 0% — but see below |
| `tomcat_threads_busy_threads` | 1.0 | 200 | effectively idle at this instant |
| `tomcat_global_error_total` | 0.0 | — | zero connector-level errors for the whole process |
| `tomcat_global_request_seconds_count` | 23552 | — | = 23363 (`/chat/stream`) + 187 (`/actuator/prometheus`) + 2 (`/healthz`) — exact accounting, see `population-accounting.md` |
| `tomcat_servlet_request_seconds_sum` / `_count` | 2.266s / 23553 | — | **~96 microseconds average time spent in the servlet/dispatcher layer per request** |
| Gateway `process_cpu_usage_mean` (whole run) | 0.0367 | 1.0 (per core) | ×10 cores ≈ **0.37 of 10 cores used on average** |
| `fd.open_peak` | 9647 | `process_files_max_files`=1,048,576 | far from any ceiling |

`tomcat_threads_current_threads=200` at final scrape means the pool *did* grow to its configured
max at some point during the run (unsurprising under load), but `tomcat_threads_busy_threads=1` at
that same instant, and `tomcat_servlet_request_seconds_sum/count ≈ 96µs/request` average, show that
individual requests occupy a Tomcat connector thread only extremely briefly — consistent with the
async-dispatch lifecycle confirmed in `async-lifecycle-audit.md` (the connector thread is released
back to the pool as soon as `ChatController.stream()` returns, not held for the request's full
7–28s streaming lifetime).

## Verdict on the Tomcat-connector hypothesis

**Ruled out.** If Tomcat's `maxThreads=200`/default `acceptCount` were binding — i.e., if requests
were queueing or being refused before Tomcat's connector could process them — `tomcat_global_
request_seconds_count` would be *lower* than the true client arrival attempts (a gap would appear at
the connector layer itself, before any servlet code runs). Instead, `tomcat_global_request_seconds_
count` (23552) matches the servlet/app/Mock/Gateway-outcome counters (23363 `/chat/stream` +
189 non-benchmark traffic) **exactly, with zero discrepancy**. Every request Tomcat's connector has
any record of receiving was fully processed and completed successfully. Combined with near-idle CPU
(0.37/10 cores) and 97% connection-count headroom, there is no resource-pressure signal anywhere in
the Gateway process. The population gap documented in `population-accounting.md` (28800 k6-reported
vs. 23363 Gateway-observed) occurs entirely **outside** anything this JVM process can see or control.
