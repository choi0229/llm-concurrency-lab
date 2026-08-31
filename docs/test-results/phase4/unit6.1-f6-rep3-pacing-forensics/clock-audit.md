# Clock-source audit and cross-metric triangulation — F6-rep3

Three independent measurement points exist for the same request lifecycle, each with its own
clock source. Read directly from raw artifacts, not estimated.

| Layer | Clock source | Susceptible to wall-clock step? |
|---|---|---|
| Bash harness (`PROCESS_START_MS`/`K6_WALL_START_MS`/`K6_WALL_END_MS`, `now_ms()` = `python3 -c 'time.time()*1000'`) | wall clock (`time.time()`) | Yes |
| k6 script (`Date.now()` in `04-phase4-closed-wave.js`'s `default()`/`client_wave_start_epoch_ms`) | wall clock (`Date.now()`) | Yes |
| Mock request log (`logging` module default formatter, "stream requested"/"completed" lines) | wall clock (`time.time()`, Python `logging` default) | Yes |
| **Mock internal Prometheus histograms** (`mockllm_stream_duration_seconds`, `mockllm_first_chunk_latency_seconds`, both `time.monotonic()`-based) | **monotonic clock** | **No** — cannot run backward or skip forward from an external step |

## The decisive cross-check

`mockllm_stream_duration_seconds` (monotonic, computed inside the exact same request lifecycle
Mock itself serves) for all four M3 N=640 runs:

| Run | Mock-internal avg stream duration (monotonic) | Client-observed (k6, wall-clock) duration p95 |
|---|---|---|
| Canary | 7.902s | 8.076s |
| F6-rep1 | 8.079s | 8.227s |
| F6-rep2 | 8.004s | 8.142s |
| **F6-rep3** | **8.098s** | **6.337s** |

F6-rep3's monotonic-clock measurement (8.098s) is statistically indistinguishable from its three
siblings (7.90-8.08s) — **the actual, physical, monotonic-time processing of every request in
F6-rep3 was completely normal**, consistent with the frozen workload's 7800ms theoretical floor
plus the same overhead seen in every other run. It is only the *wall-clock-based* observations
(k6's `Date.now()`, Mock's `logging` timestamps) that show the ~6.1-6.35s compression.

## Outer wall-clock span corroborates a real clock step, not a single wall-clock read glitch

The bash-harness-measured total k6 process wall-clock span (`k6_wall_end_ms - k6_wall_start_ms`,
also `time.time()`-based, wrapping the *entire* k6 subprocess including 5 sequential prewarm
requests + the 640-wide wave):

| Run | k6 process wall span |
|---|---|
| Canary | 47.727s |
| F6-rep1 | 47.979s |
| F6-rep2 | 47.911s |
| **F6-rep3** | **46.073s** |

F6-rep3's outer wall span is ~1.85-1.9s shorter than its siblings — closely matching the
~1.7-2.0s gap between F6-rep3's monotonic-based (8.098s) and wall-clock-based (6.337s) per-request
duration. **Three separate wall-clock observation points (bash harness, k6 `Date.now()`, Mock's
Python `logging`) all show a consistent compression of roughly the same magnitude, uniquely in
the F6-rep3 window, while the one monotonic-clock observation shows nothing unusual at all.** This
is the signature of a host wall-clock adjustment (e.g., an NTP correction or system time sync)
occurring during F6-rep3's ~46-second run window, not a per-measurement glitch or a genuine change
in processing speed.

## What this audit does not have

Direct host NTP/system-clock-step logs (e.g., macOS `system.log`/`ntpd` records) were not
consulted — out of scope for what the existing Formal run artifacts capture, and no new
instrumentation was added per instruction. The conclusion above rests entirely on cross-metric
triangulation within the existing raw evidence (monotonic vs. wall-clock divergence, corroborated
across three independent wall-clock-based layers), which is treated as sufficient given the
Mock's monotonic-clock histogram is proof, not inference, that the real physical elapsed time was
normal.

## 150ms-interval hypothesis: not supported

`1000 + 34*150 = 6100`ms is numerically close to the observed wall-clock-based duration
(6.094-6.347s), but Mock's own monotonic-clock `first_chunk_latency` (1.0011s, matching its
siblings exactly) and `stream_duration` (8.098s, matching its siblings) histograms directly
disprove any actual shortened `chunkIntervalMs` or `firstChunkDelayMs` having been used — the real
elapsed processing time was ~8.1s, not ~6.1s. The numeric proximity to the 150ms-interval
hypothesis is coincidental, not causal.
