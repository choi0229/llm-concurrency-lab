# BindException audit — `m2-r256-screen1` (existing canonical raw, read-only)

## What is in the raw

`docs/test-results/phase4/unit8-open-screening/m2-r256-screen1/gateway.log` — **9** lines, all
identical shape:

```
2026-08-30T13:36:23.822+09:00  INFO 44181 --- [  virtual-16647] c.l.phase4.common.BlockingMockLlmRelay :
    [p4-16506] upstream relay ended with IOException: java.net.BindException: Can't assign requested address
```

Timestamps (all inside the measurement window `13:35:11`–`13:38:19`):

```
13:36:23.822   p4-16506   virtual-16647
13:36:29.494   p4-16653   virtual-16794
13:37:03.130   p4-24745   virtual-24886
13:37:07.780   p4-24877   virtual-25018
13:37:12.229   p4-25518   virtual-25659
13:37:29.670   p4-29705   virtual-29846
13:37:43.310   p4-32981   virtual-33122
13:37:47.752   p4-33260   virtual-33401
13:37:51.686   p4-33762   virtual-33903
```

`result.json` → `server_outcomes_whole_process_estimate.upstream_error = 9.0`. Everything else in
that estimate (`timeout`, `client_disconnect`, `internal_error`, `write_overflow`) = 0.
`completed` (whole process) = 38463.

## Exact layer — NOT determinable from this raw, only from source

`BlockingMockLlmRelay.relay()`'s `catch (IOException e)` logs **`e.toString()` only**:

```java
log.info("[{}] upstream relay ended with IOException: {}", lifecycle.requestId(), e.toString());
```

There is **no stack trace** in `gateway.log`. §16's "full stack trace preserved" requirement
**cannot be met from the existing artifact** — it needs a fresh diagnostic run with the catch block
logging `e` (not `e.toString()`), or `-Djava.net.debug` / `sun.net.www` FINEST.

From JDK source (see `jdk-httpurlconnection-audit.md`), `java.net.BindException: Can't assign
requested address` = `EADDRNOTAVAIL` from `connect(2)` failing to auto-assign an ephemeral **source**
port (no explicit `Socket.bind()` is in the path). Frame, by source reasoning:

`MockLlmClient.openStream()` → `HttpURLConnection.getInputStream()` … actually thrown earlier, at
`connection.getOutputStream()` / first `connect()`:
`HttpURLConnection.connect()` → `HttpClient.New()` (cache miss) → `new HttpClient` →
`HttpClient.openServer()` → `NetworkClient.doConnect()` → `SocketImpl.connect()` → native
`connect()` → `EADDRNOTAVAIL`. **Implicit source-port bind inside `connect()`, not an explicit bind
call.**

## Magnitude — 9 cannot explain 5688

- Client measurement cohort: `actual_started = 30721`, `completed = 25033`,
  **`failed_no_event = 5688`**, `failed_mid_stream = 0`, `rejected = 0`, `dropped_iterations = 0`.
- The 9 Gateway `BindException`s map to `upstream_error = 9` ≈ **0.16%** of the 5688.
- `k6-stdout.log`: **0** `sse error` console lines; `client.on('error')` never fired for any of the
  5688. `vus.max = 2096` vs `PRE_ALLOCATED_VUS = 15360` → not a k6 VU-pool exhaustion.
- `http_reqs = 46081 = iterations` (whole run) and `http_req_duration.min = 486µs` → every iteration
  opened exactly one HTTP request object, and a population of them terminated in < 1 ms with no SSE
  event and no error callback — the Unit 7.2/7.4 signature of a **connect-stage failure on the
  k6→Gateway leg** (the xk6-sse setup callback never runs, so nothing JS-visible fires).

So the 5688 client failures are **on the k6→Gateway leg (LEG A)**, a different leg from the 9
Gateway→Mock `BindException`s (LEG B). The 9 are a same-class `EADDRNOTAVAIL` symptom on LEG B; they
are corroborating evidence that the host ephemeral-port pool was under pressure during the run, not
the direct cause of the 5688.

## Cross-run uniqueness

`grep -rl "BindException" docs/test-results/phase4/unit8-open-screening/*/gateway.log` → **only
`m2-r256-screen1`**. Zero at R=160 and below for M2, zero anywhere for M3. Reproducible threshold
effect at the top of the frozen rate range, not noise.

## Transient shape (from k6-stdout + prom)

The k6 VU count and `complete` counter show a sharp dip/stall at ~t=33–46 s of the run
(VUs collapse ~2050→~400, `complete` frozen at 10203 for ~5 s) then recovery — consistent with a
burst of fast-failing iterations (freeing VUs) during a window of port pressure, then TIME_WAIT
ageing out and load resuming. Gateway `virtual_tasks_active` / `gateway_active_requests` stay ~1630
throughout (no backlog growth), so the stall is **outside** the Gateway, on the connection-
establishment path.
