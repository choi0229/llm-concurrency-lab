# M2 R=160 (screen1) — Population Accounting

All figures are read directly from existing artifacts in
`docs/test-results/phase4/unit7-open-screening/m2-r160-screen1/`. No new load was run. No number
below is estimated/guessed unless explicitly marked "(derived)".

## Stage-by-stage counted population (whole process lifetime, 00:23:31–00:26:42)

| Stage | Source | Value | Matches |
|---|---|---|---|
| A/B. k6 iterations scheduled+started (all phases) | `k6-summary.json` `iterations.count` | **28800** | = `http_reqs.count` (28800) |
| C/D. k6 HTTP/SSE "request" completed (client's own transport accounting) | `k6-summary.json` `http_reqs.count`, `http_req_duration.count` (28800 samples, all in the 7.77s–28.04s range, no near-zero/failed cluster) | **28800** | — |
| E. Tomcat connector accepted+processed (all URIs) | `gateway-metrics-final.txt` `tomcat_global_request_seconds_count{name="http-nio-18102"}` | **23552** | = 23363 (`/chat/stream`) + 187 (`/actuator/prometheus`) + 2 (`/healthz`) |
| F. ChatController.stream() invoked (line 63, `metrics.requestStarted()`, the very first statement in the method) | `gateway_requests_started_total` | **23363** | = E's `/chat/stream` component exactly |
| G. VirtualTaskSubmitter.trySubmit() called (M2's unconditional-accept dispatch point) | `virtual_tasks_started_total` | **23363** | = F exactly |
| H. Mock received the relayed request | `mock-metrics-final.txt` `mockllm_first_chunk_latency_seconds_count` | **23363** | = G exactly |
| I. Mock completed the stream | `mockllm_completed_requests_total` | **23363** | = H exactly |
| J. Gateway terminal outcome recorded (any outcome) | `gateway_requests_total` sum across all `outcome` labels | **23363** (all under `outcome="completed"`, every other label = 0.0) | = I exactly |
| K. Client terminal outcome (measurement phase only, 120s) | `client_cohort`: completed=15072, failed_no_event=4127, rejected=0, failed_mid_stream=0 | 19199 total | dispatched subset of J that fell inside the measurement window |

## The gap

**28800 (client-believed request count) − 23552 (Tomcat connector count, all URIs) = 5248**, or
equivalently **28800 − 23363 = 5437** against the `/chat/stream`-only count.

This gap exists **entirely upstream of Tomcat's own connector accounting** — every stage from E
through J is in exact 1:1 agreement (Tomcat connector → Servlet dispatch → app dispatch → Mock
receipt → Mock completion → Gateway terminal-outcome recording). There is **no internal Gateway/JVM
stage where the count changes**. The only stage where the population differs from 23363 is k6's own
client-side iteration/http_reqs count (28800).

## What this rules out

- **Tomcat thread-pool/accept-queue starvation** (`server.tomcat.threads.max`/`accept-count`
  binding): if true, Tomcat's own connector-level counter (`tomcat_global_request_seconds_count`)
  would show fewer *processed* requests than the true arrival attempts, i.e., a gap would appear
  **at or before stage E**. It does not — E already equals F/G/H/I/J exactly. Tomcat processed
  every request it has any record of receiving.
- **Application-level silent drop** (e.g. an exception swallowed before a metric increments): ruled
  out by F=G=H=I=J with zero variance across five independent counters maintained by three
  different processes (Gateway JVM, Mock FastAPI process, and Gateway's own Tomcat connector layer).
- **A Gateway resource ceiling** (`maxConnections=8192`, `tomcat_connections_current_connections`
  peaking only at 244 in the final snapshot; Gateway CPU usage averaging ~0.37 of 10 cores for the
  whole run — see `tomcat-metrics-audit.md`): no evidence of Gateway-side saturation of any kind.

## What remains open

The 5437-count gap is between "k6 believes it made an HTTP/SSE request with a plausible non-error
duration" and "Tomcat's connector has any record of it at all." This is outside what any Gateway,
Mock, or Prometheus artifact can observe — it is either a client-side (`k6/x/sse` extension)
metric-emission artifact, or a network-path event between the k6 process and the Gateway's listening
socket that neither side's application-level instrumentation captures. See
`k6-transport-errors.md` for the client-side evidence search and `backlog-math.md` for why the
throughput-deficit arithmetic is a separate (and separately real) phenomenon from this population
gap.
