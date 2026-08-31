# Event count audit — F6-rep1/rep2/rep3

From each run's `k6-summary.json` (raw k6 metrics, not recomputed):

| Run | `sse_event` count | `client_stream_completed_total` | events/completed | `http_reqs` |
|---|---|---|---|---|
| F6-rep1 | 23220 | 640 | 36.28125 | 645 |
| F6-rep2 | 23220 | 640 | 36.28125 | 645 |
| F6-rep3 | 23220 | 640 | 36.28125 | 645 |

**Byte-identical across all three reps.** `http_reqs=645` = 640 wave + 5 prewarm (all use the same
frozen `chunkCount=35`), and `23220 = 645 * 36` exactly (35 delta + 1 final per request, per the
source-code-derived formula in `mock-pacing-source-audit.md`). No missing/extra events for
F6-rep3 relative to its siblings — `chunkCount` was applied identically in all three runs. This
rules out any explanation involving a reduced number of chunks (e.g., early termination or a
different `chunkCount`) for F6-rep3.

No per-event (per-chunk) timestamp is recorded anywhere in the existing raw artifacts (k6 only
aggregates `sse_event` as a Counter, not a per-event Trend with timestamps; Mock's log only
records "stream requested" and "completed", not each intermediate `delta` chunk). **Direct
inter-chunk interval reconstruction (item 9 of the forensics brief) is not possible from the
existing artifacts** — stated explicitly per instruction not to force an estimate from
insufficient data. The closest available evidence is the aggregate first-chunk-latency and total
stream-duration histograms, both addressed in `mock-pacing-source-audit.md` and `clock-audit.md`.
