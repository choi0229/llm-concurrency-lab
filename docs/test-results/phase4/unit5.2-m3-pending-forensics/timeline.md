# M3 N=400 (screen1) — pending-sample timeline reconstruction

All timestamps epoch seconds, from raw Prometheus range-query JSON already collected during the
original run (no new load). `rel_wave_s` is relative to the canonical wave's actual start,
computed from k6's own client-side instrumentation, not estimated.

## Anchor timestamps (from timestamps.json / k6-summary.json — exact, not estimated)

| Event | epoch ms | epoch s |
|---|---|---|
| Gateway/Mock/Prometheus process start | 1787921304682 | 1787921304.682 |
| k6 process launch (`k6 run`, includes `setup()` prewarm) | 1787921306401 | 1787921306.401 |
| Canonical wave actual start (`client_wave_start_epoch_ms`, k6 summary: min=1787921345899, max=1787921345922) | ~1787921345899–1787921345922 | ~1787921345.91 (spread 23ms) |
| k6 process end | 1787921354249 | 1787921354.249 |
| Drain start / end | 1787921354265 / 1787921354291 | (26ms drain) |

**Prewarm duration derived, not assumed**: k6 launch (1787921306.401) to wave start
(~1787921345.91) = **39.5s** for 5 sequential prewarm streams — consistent with each prewarm
stream taking ~7.9s (identical workload params to the wave: `firstChunkDelayMs=1000,
chunkIntervalMs=200, chunkCount=35` ⇒ ~7.9–8.1s per stream, matching the wave's own observed
`client_stream_duration_seconds` min/max of 7.885/8.095s). This is a derived cross-check, not a
guess: 5 × ~7.9s ≈ 39.5s matches the observed gap almost exactly.

## Second-by-second correlation (raw values, see `timeline-raw.csv`)

| epoch_s | rel. to wave start | pending | active_conn | gw_active_requests | jvm_threads | fd_open |
|---|---|---|---|---|---|---|
| 1787921340..1787921345 | −5.91s..−0.91s | 0 | 1 | 1 | 25 | 47 |
| **1787921346** | **+0.09s** | **1** | **1** | **11** | 25 | 80 |
| 1787921347 | +1.09s | 0 | 400 | 400 | 30 | 846 |
| 1787921348..1787921351 | +2.09s..+5.09s | 0 | 400 | 400 | 30 | 846 |

## Interpretation

- For the ~40s of prewarm (and the 1s immediately before wave start), the pool held exactly
  **1** active connection (the single sequential prewarm VU) and **0** pending. No pending event
  occurred during prewarm.
- The very first Prometheus scrape *after* the wave's actual start (`+0.09s` relative to
  `client_wave_start_epoch_ms`) shows `gateway_active_requests=11` — i.e. only 11 of the 400 wave
  requests had reached the Gateway by that scrape instant, `active_conn` still at 1 (the pool had
  not yet finished opening new connections), and `pending=1`.
- One second later (`+1.09s`), `active_conn` and `gateway_active_requests` both read exactly
  `400` — the pool fully ramped up, and pending returned to `0` and never left it again for the
  rest of the run (see `pending-samples.csv`: every other one of the 45 samples is `0`).
- `fd_open` rose from 47 → 80 → 846 across the same three samples, tracking the same ramp.

This is a single 1-second snapshot landing exactly in the middle of the pool's 1→400 connection
ramp-up, triggered by 400 simultaneous `acquire()` calls hitting a pool that had only 1 warm
connection going in (prewarm is sequential and single-VU, so it never grew the pool beyond 1).
`gateway_active_requests=11 > 0` at the pending instant rules out a "phantom pending with no live
traffic" scenario (docs/... forensics brief §8) — real wave traffic was in flight when pending
was observed.

## Client-side outcome check (rules out any hidden long-tail wait)

From `k6-summary.json` (`client_completed_stream_duration_seconds` / `http_req_duration`):

- min 7.885s / 7.879s, med ~8.07s, avg ~8.06s, p90 8.08s, **p95 8.082s**, **max 8.095s / 8.0949s**
- k6's summary export does not include p99 as configured, but max (8.095s) is an upper bound on
  every one of the 400 completed requests, and it sits only ~13ms above p95 — there is no
  long-tail request anywhere near the 20–30s range hypothesized in the forensics brief. All 400
  requests completed within an 8.095s−7.885s = 210ms band of each other.
- `completed=400/400`, `rejected=0`, `failed_mid_stream=0`, `failed_no_event=0` (from
  `result.json` / `client_cohort`) — reconfirmed, unchanged from the original report.

This directly answers forensics brief §7: the single pending sample is **not** correlated with
any request that waited unusually long — it is a ramp-up-instant artifact affecting (at most) one
of 400 nearly-simultaneous connection acquisitions, resolved within one second.
