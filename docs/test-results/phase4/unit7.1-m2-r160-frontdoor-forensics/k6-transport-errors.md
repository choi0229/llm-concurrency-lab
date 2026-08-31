# M2 R=160 (screen1) — k6 Client Transport Error Audit

Source: `docs/test-results/phase4/unit7-open-screening/m2-r160-screen1/k6-stdout.log` (617 lines,
full file grepped, not sampled), `k6-summary.json`.

## Explicit error search

Grepped for: `sse error` (the literal string the scenario's own `client.on('error', ...)` handler
logs via `console.log`), plus `level=error`, `level=warning`, `ERRO[`, `WARN[` (k6's own runtime log
prefixes).

**Result: zero matches for all of the above.** Not one of the 4127 `failed_no_event` (measurement
phase) iterations — nor any iteration in the 60s warmup phase — produced a console log line of any
kind. k6's own runtime logged no warnings or errors for the entire test.

## What this means

The scenario script (`05-phase4-open-arrival-rate.js`) only reaches its `failed_no_event` branch
when: `res` is falsy or `res.status !== 503`, AND `eventCount === 0` (i.e., `client.on('event', ...)`
never fired). The `client.on('error', ...)` handler — the only place the script logs anything — is
registered *inside* the `sse.open()` callback, which only runs once a session object exists. If
`sse.open()` itself returns without ever invoking that callback (e.g., because the underlying
connection attempt never reached a state where the extension considers a "session" to exist), no
error is logged, no event fires, and the iteration silently falls through to `failed_no_event` —
with whatever elapsed wall-clock time `sse.open()` happened to take counted as its "duration."

## Built-in metric cross-check

`http_req_duration` / `iteration_duration` / `http_reqs` all report **count=28800** for the whole
test, with **no distinguishable failure cluster**: minimum duration is 7.77s (in line with the
workload's own minimum legitimate stream time: `firstChunkDelayMs=1000ms` + 35 chunks ×
`chunkIntervalMs=200ms` ≈ 8000ms), maximum 28.04s, p95 10.3s. If a meaningful fraction of the 28800
were fast client-side connect failures (refused/reset), we would expect a second mode near 0s in
`http_req_duration`'s distribution; there is none — `min=7772ms` for the *entire* 28800-sample
population, not just the ones that reached the server. k6 does not expose a separate `http_req_failed`
metric in this run's summary at all, which is consistent with the `k6/x/sse` extension not routing
`sse.open()` through k6's standard HTTP-failure-tracking path.

## VU/capacity check (ruling out loadgen pool exhaustion as the cause of *this* run)

- `vus.max = 1788` (peak concurrently-active VUs), well under both `PRE_ALLOCATED_VUS=3000` and
  `MAX_VUS=8000` for this run (`environment.json`).
- `client_cohort.dropped_iterations = 0` — k6's own executor never failed to allocate a VU for a
  scheduled iteration.

This rules out the *specific* VU-pool-sizing bug found and fixed earlier in this project (the
`PRE_ALLOCATED_VUS`/`MAX_VUS` formula fix) as the cause here — that fix is confirmed still in effect
and was not the limiting factor for this run.

## Conclusion

No error is logged anywhere in the client's own runtime for any of the 4127 measurement-phase
failures. The only way to reconcile "k6 reports a normal, non-error, plausible-duration iteration"
with "Tomcat has zero record of it" is a failure mode the extension does not classify as an error at
all — most plausibly a connection-establishment-level event (e.g., local socket/port pressure on the
k6 process, or a loopback-specific network condition) that happens *before* the `sse.open()` callback
session is created, which this script's own logic silently maps to `failed_no_event` rather than to
a distinguishable error path. This cannot be confirmed further from the artifacts on hand — no
client-side (k6 process) OS/socket telemetry was captured during the run (the harness only samples
Gateway-side RSS/FD/CPU; see `tomcat-metrics-audit.md`). This is a genuine evidence gap, disclosed
here rather than resolved by inference.
