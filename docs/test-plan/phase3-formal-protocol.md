# Phase 3 Formal Measurement Protocol (Unit 6.5 — Freeze)

Status: Protocol frozen. Harness/collector written and statically reviewed. **Canary NOT run.
27-run Formal matrix NOT run.** Execution requires separate, explicit user approval per run tier
(§14/§Unit 6.5 execution scope). Phase 1/2 canonical results are not touched by this Unit.
Date: 2026-08-23

## 0. Source of truth re-confirmed this Unit

Re-read before freezing anything below: `docs/test-plan/phase3-design.md`,
`docs/decisions/phase3-version-compatibility.md`, `docs/decisions/phase3-metrics-contract.md`,
`docs/decisions/phase3-admission-semantics-unification.md`,
`docs/decisions/phase3-admission-connection-pool.md`,
`docs/test-results/phase3/unit5-cross-validation/`,
`docs/test-results/phase3/unit5.5-diagnostics/SUMMARY.md`,
`docs/test-results/phase3/unit6-screening/SUMMARY.md`. Runtime versions, resolved dependency jars
inside each config's actual built `bootJar`, JDK build string/checksum, Gradle wrapper version,
host OS/arch, and k6/xk6-sse version were re-verified directly (not recalled from memory) —
see §8/§Formal environment.json below for the exact values captured.

## 1. Formal research questions (reconfirmed)

**Experiment A** — P3-A (MVC + `HttpURLConnection` blocking outbound + Servlet write workers) vs.
P3-B (MVC + WebClient/Reactor Netty outbound + Servlet write workers): holding the Servlet response
model constant, what changes when the blocking outbound worker pool is removed? (platform threads,
CPU, RSS, client TTFC, client total stream duration, rejection/completion capacity.)

**Experiment B** — P3-B vs. P3-C (WebFlux + WebClient + fully reactive response, no Servlet layer):
holding the outbound WebClient constant, what changes when the Servlet response/write layer is
also removed? (same six axes.)

Not a three-way leaderboard. Each experiment is a controlled, one-variable-at-a-time comparison;
P3-A vs. P3-C is never compared directly as a "which is best" question — only via the two
pairwise experiments above.

## 2. Formal load freeze

```
R3  = 3 req/s   -- Stable            (Unit 6 Screening: 0/76 rejected, all three configs)
R7  = 7 req/s   -- Near-capacity     (Unit 6 Screening: ~15/175 rejected, all three configs)
R10 = 10 req/s  -- Clear overload    (Unit 6 Screening: ~87/250 rejected, all three configs)
```

Identical rates for all three implementations — no per-implementation rate tuning. Source:
`docs/test-results/phase3/unit6-screening/SUMMARY.md` §6/§8, approved by the user this Unit.

## 3. Formal matrix

```
3 configs (P3-A, P3-B, P3-C) x 3 loads (R3, R7, R10) x 3 repeats = 27 valid measured runs
```

An invalid run (§13) does not count toward a cell's 3 repeats — it is retried in a fresh
environment under the same (config, load, repeat-slot) label, and both the invalid and the retry
artifacts are preserved (§13).

## 4. Run order (frozen — not to be changed after this Unit)

Reused directly from the validated Phase 2 native-Formal precedent
(`scripts/run-phase2-native-formal-matrix.sh`'s "interleave configs within a rep, rotate which
config starts each rep, keep rate order fixed within a rep" rule), extended from 2 to 3 configs
with a cyclic rotation (A,B,C / B,C,A / C,A,B) so no config is disadvantaged by always running
first or last, and time-of-day/thermal/host-state drift does not correlate with any one config.
Rate order is fixed `R3 -> R7 -> R10` within every repeat (matches Phase 2's precedent; only the
config rotation varies rep-to-rep).

```
Repeat 1 (A,B,C):
 1. A-R3-run1   2. B-R3-run1   3. C-R3-run1
 4. A-R7-run1   5. B-R7-run1   6. C-R7-run1
 7. A-R10-run1  8. B-R10-run1  9. C-R10-run1

Repeat 2 (B,C,A):
10. B-R3-run2  11. C-R3-run2  12. A-R3-run2
13. B-R7-run2  14. C-R7-run2  15. A-R7-run2
16. B-R10-run2 17. C-R10-run2 18. A-R10-run2

Repeat 3 (C,A,B):
19. C-R3-run3  20. A-R3-run3  21. B-R3-run3
22. C-R7-run3  23. A-R7-run3  24. B-R7-run3
25. C-R10-run3 26. A-R10-run3 27. B-R10-run3
```

This exact sequence is encoded literally (not computed by a rotate-at-runtime algorithm) as the
`RUNS` array in `scripts/run-phase3-native-formal-matrix.sh`, so execution order cannot silently
drift from what's frozen here. It is not to be reordered after this Unit without a new ADR-level
decision.

## 5. Fresh process isolation

Every measured run (Canary included): fresh Gateway JVM, fresh Mock LLM process, no Prometheus
TSDB reuse (this harness — like Phase 2's native harness and Phase 3's Screening harness — reads
`/actuator/prometheus` directly rather than running a separate Prometheus server; "fresh
Prometheus" here means fresh in-JVM Micrometer registry state, which a fresh JVM already
guarantees). No JVM/Mock/registry state survives from one run to the next.

Preflight (`scripts/run-phase3-native-formal-benchmark.sh`, before starting anything): target
Gateway port free, Mock LLM port free, no stale PID/process matching the target jar's basename or
`uvicorn app.main:app`, Docker Desktop not running (`pgrep -f "Docker Desktop|com.docker.backend"`
must find nothing — reused from the Phase 2 native preflight pattern). Postflight (after drain,
before the next run starts): all gauges 0 (§11), both processes confirmed terminated.

## 6. Native environment

macOS arm64 native only — no Docker Desktop, no Rosetta, no QEMU/emulation. `caffeinate -dims`
held for the duration of each run (reused from `scripts/run-phase2-native-formal-benchmark.sh`).
No unrelated heavy workload running concurrently (checked in preflight by the same Docker Desktop
check plus a manual operator confirmation step — Formal execution, not this Unit, is where that
confirmation actually happens). Power source recorded in `environment.json` per run
(`pmset -g batt`) — informational; AC power is preferred for thermal stability but the harness
does not hard-fail on battery power, since throttling shows up in the CPU/thread data itself and
the postflight/validity gates would catch a genuinely degraded run some other way.

## 7. Runtime freeze — re-verified this Unit, not recalled

| Component | Frozen value | How verified this Unit |
|---|---|---|
| JDK | Zulu 8.96.0.205-CA (`1.8.0_504`, build `1.8.0_504-b01`), macOS aarch64 | `java -version` on `.native-runtime/zulu8.96.0.205-ca-jdk8.0.504-macosx_aarch64` directly |
| JDK binary SHA-256 | `b49708ba8e68dae58e1aad83ede16b73b4b2cfc4294dcd95a2ad3964c92cae5f` (of `bin/java`) | `shasum -a 256` this Unit — recorded in every run's `environment.json` |
| Gradle wrapper | 7.6.6 | `gradle-wrapper.properties` |
| Spring Boot | 2.7.18 | `build.gradle` plugin version |
| Spring Framework | 5.3.31 | resolved `spring-core-5.3.31.jar`/`spring-web-5.3.31.jar` inside the actual built `bootJar` |
| Reactor Core | 3.4.34 | resolved `reactor-core-3.4.34.jar` inside the actual built `bootJar` |
| Reactor Netty | 1.0.39 | resolved `reactor-netty-core-1.0.39.jar`/`reactor-netty-http-1.0.39.jar` inside the actual built `bootJar` |
| Micrometer | 1.9.17 | resolved `micrometer-core-1.9.17.jar` inside the actual built `bootJar` |
| Tomcat (P3-A/B only) | 9.0.83 | resolved `tomcat-embed-core-9.0.83.jar` inside the actual built `bootJar` |
| k6 | v1.8.0 (go1.26.6, darwin/arm64) | `.native-runtime/k6 version` |
| xk6-sse | v0.1.11 | same command's extension listing |
| Mock LLM | current `mock-llm-fastapi` on repo HEAD, Python 3.12 venv | unchanged since Unit 5/6 |

No version upgrade in this Unit. Every run's `environment.json` records these values again at
run time (not assumed to still match by the time Formal actually executes).

## 8. Formal config freeze

Common: `MOCK_LLM_BASE_URL=http://127.0.0.1:8000`, `CHAT_ADMISSION_LIMIT=50`,
`CHAT_TOTAL_TIMEOUT_MS=60000`.

Normal Mock workload (unchanged from Screening/Phase 2 Formal):
`firstChunkDelayMs=1000, chunkIntervalMs=200, chunkCount=35, chunkSizeBytes=64`.

| Axis | Value | Source (re-verified this Unit) |
|---|---|---|
| P3-A `CHAT_BLOCKING_POOL_SIZE` | 50 | set explicitly (= admission ceiling); code default already falls back to `admissionGate.limit()` |
| P3-A/B `SERVLET_WRITE_POOL_SIZE` | 8 | `ExecutorConfig.SERVLET_WRITE_POOL_SIZE_DEFAULT`, left at code default in Screening — same here |
| P3-A/B `SERVLET_WRITE_QUEUE_CAPACITY` | 64 | `ExecutorConfig.SERVLET_WRITE_QUEUE_CAPACITY_DEFAULT`, same |
| P3-A/B `SERVLET_PER_STREAM_BUFFER_CAPACITY` | 32 | `ChatController.PER_STREAM_BUFFER_CAPACITY_DEFAULT`, same |
| P3-B/C `WEBCLIENT_MAX_CONNECTIONS` | 50 | set explicitly (= admission ceiling); code default already falls back to `admissionGate.limit()` |
| P3-B/C `WEBCLIENT_PENDING_ACQUIRE_MAX_COUNT` | 1 | `WebClientConfig.PENDING_ACQUIRE_MAX_COUNT_DEFAULT`, left at code default |
| P3-B/C `WEBCLIENT_PENDING_ACQUIRE_TIMEOUT_MS` | 1000 | `WebClientConfig.PENDING_ACQUIRE_TIMEOUT_MS_DEFAULT`, same |
| P3-B/C `WEBCLIENT_CONNECT_TIMEOUT_MS` | 3000 | `WebClientConfig.CONNECT_TIMEOUT_MS_DEFAULT`, same |
| P3-B/C `WEBCLIENT_LOOP_THREADS` | unset (= `Runtime.availableProcessors()` = 10 on this host) | code default; identical on both since same machine |

These are exactly the values Unit 6 Screening actually ran with — not re-guessed. No config axis
changes between Screening and Formal.

## 9. Admission canonical meaning (carried over, not re-litigated)

`gateway_admission_active` = requests currently holding a Mock LLM upstream-call permit. Acquire:
immediately before the upstream call starts. Release: first of upstream complete/error/cancel.
Never waits on client physical receive. Full definition: `docs/decisions/
phase3-admission-semantics-unification.md`.

## 10. `gateway_active_streams` — restricted to implementation-local diagnostic

**Not used as a cross-implementation Primary capacity metric.** P3-A/B's terminal is the Servlet
write-channel drain boundary; P3-C's is the reactive response Publisher boundary, which Unit 5.5's
Slow Client diagnostic measured can precede client physical receive completion by seconds. So
"P3-C's `active_streams` is lower" must never be read as "P3-C has fewer actual open client
connections" — the two implementations' `active_streams` are not measuring the same physical
event. Formal Primary resource/capacity comparison uses only: JVM platform threads, CPU, RSS,
`gateway_admission_active`, and client-side (k6) metrics — all four of which mean the same
physical thing in all three implementations.

## 11. Warm-up — same rate as measurement, continuous lifecycle

Screening's "5 small fixed requests" warm-up is Screening-only and is **not** reused for Formal.
Formal warm-up runs at the **same arrival rate as the measured load** (R3 warm-up is 3 req/s, R7
warm-up is 7 req/s, R10 warm-up is 10 req/s) — same Normal Mock workload body as measurement.

**Continuous lifecycle** (mirrors Phase 2's validated fix, `docs/test-plan/
phase2-formal-protocol.md` §3, for the identical reason: Phase 2 found that a separate
warm-up-then-drain-then-fresh-measurement-process design starts the measurement window against an
empty system, and because the Normal Mock workload takes ~7.8s to complete, no request can
complete until ~7.8s into the window — a structural under-count of completion throughput that is
an artifact of the two-process design, not a real capacity signal): warm-up and measurement run in
**one continuous k6 process**, no process kill/drain in between. `scripts/phase3-formal-k6.js`
(new, Phase-3-specific — Phase 2's `03-constant-arrival-rate-continuous.js` is read for the
technique but not modified or imported) uses the same single-clock-read pattern Phase 2 converged
on: `setup()` reads `Date.now()` once, computes `measurementStartMs = now + WARMUP_SEC*1000`, and
emits it as a `Gauge` (`measurement_start_epoch_s`) so the shell harness reads the *same* value
back out of the k6 summary export after the process exits, instead of independently recomputing it
(the exact double-clock-read bug Phase 2 found and fixed, `docs/test-plan/phase3-formal-protocol.md`
being written fresh this time specifically avoids reintroducing it).

Per-iteration phase classification is by **arrival time** (`start >= measurementStartMs`), matching
the Unit 5/Phase 2 outcome-cohort convention of classifying by request start time, not completion
time — an iteration that starts in warm-up but finishes after the boundary is still excluded; one
that starts right at the boundary is included even though it finishes later. Warm-up iterations
still send real requests (keep the Gateway JVM's JIT/connection pools and this k6 process's own
VU/connection pool warm) but are never recorded into any Formal client-side metric.

`WARMUP_SEC = 120`, `MEASUREMENT_SEC = 300` — adopted directly from Phase 2 Formal's frozen values
(`scripts/run-phase2-formal-matrix.sh`/`run-phase2-native-formal-matrix.sh`, every cell used
`120 300`). No reason found to deviate: same host class of cold-start cost (JIT, Reactor
Netty/WebClient connection pool init — Unit 3/4's own cold-start findings), same order-of-magnitude
per-request service time (~7.8s), same general Gateway/Mock architecture family.

## 12. Measurement population — two populations, not conflated

**A. Throughput population** (chronological): requests that actually **completed** with their
completion timestamp inside `[measurement_start, measurement_end)`. `completion_throughput =
count / MEASUREMENT_SEC`. Requests completing in warm-up or in drain are excluded.

**B. Client outcome cohort** (authoritative, k6-sourced — Phase 2's 2026-08-18 revision, reused
directly): all iterations whose **arrival time** is inside `[measurement_start, measurement_end)`,
tracked through to drain until each reaches a terminal outcome. `scripts/phase3-formal-k6.js`'s own
`measurement_iterations_started_total` Counter (not k6's built-in `iterations`, which counts both
phases and can't be filtered after the fact) is the cohort-size source of truth — not a Prometheus
windowed counter, for the same reason Phase 2 rejected that approach: Prometheus counters cannot
distinguish "this completion belongs to a request that arrived during warm-up" from "...during
measurement" once both can complete inside `[measurement_start, drain_end]`.

Client cohort invariant (checked per run, part of the validity gate, §13):

```
measurement_iterations_started_total
  == client_completed_total + client_rejected_total
     + client_failed_mid_stream_total + client_failed_no_event_total
```

(`failed_mid_stream`/`failed_no_event` are the actual `scripts/phase3-formal-k6.js` outcome
categories — matching the existing Phase 2/3 k6 script schema; there is no separate k6-side
"timeout" category, since a Gateway-side `timeout` outcome surfaces to the client as either
`failed_mid_stream` (events were received, then the stream stopped) or `failed_no_event`, exactly
as it already does in `scripts/phase3-screening-k6.js`.)

Prometheus's own windowed outcome counters over `[measurement_start, drain_end]` are recorded as
`server_diagnostic` only — not required to exactly equal the k6 cohort (warm-up spillover explains
any difference), used only to sanity-check for gross anomalies.

## 13. Validity gates

### 13-A. Preflight (per run)

Native arm64 confirmed; Docker Desktop not running; no unrelated heavy process; host clock read
sane (informational, no drift-correction needed on native — this is a Docker-VM-clock-drift
mitigation carried over from Phase 1/2 lessons, not expected to trigger natively); JDK
version/build/checksum matches §7; k6 exact version matches §7; Gateway config matches §8; Mock
workload matches §8; fresh Gateway JVM; fresh Mock process; target ports clean; previous run's
drain fully completed (§16) before this run's process start. Mac sleep must not occur during the
run — `caffeinate -dims` (§6) is the mitigation, not a separate check, since a `pmset`-level sleep
during a run would show up as the log-gap/plateau detector (§15) firing.

### 13-B. Runtime / postrun

`dropped_iterations == 0` (k6's own built-in metric; nonzero means the load generator itself
under-provisioned VUs, not a Gateway signal — §Unit 6 §26/§27, carried over); client cohort
invariant (§12) holds; zero `internal_error` outcomes in the client cohort or in Gateway Prometheus
counters; no counter plateau/log-gap over 20s during `[measurement_start, measurement_end]`
(§15); postflight resource cleanup clean (§16); no connection-pool pending buildup
(`reactor_netty_connection_provider_pending_connections` must stay 0 throughout for P3-B/C — a
nonzero value the whole run means WebClient's connection pool became a second, hidden admission
limiter, invalidating the run's comparability); zero `write_overflow` in P3-A/B during a Normal
(non-degenerate) Formal run — nonzero means the run accidentally became a slow-client/write-buffer
experiment rather than the intended Normal-workload capacity experiment, and must be investigated,
not silently kept; RSS sampler PID verified matching the actual Gateway process (§17-19) —
mandatory on every run, not only the Canary, since a fresh JVM gets a fresh PID every time.

Unexpected `timeout` outcomes in a Normal-workload run (R3/R7, where the ~7.8s service time is far
under `CHAT_TOTAL_TIMEOUT_MS=60000`) are validity/anomaly signals to investigate — not expected
Gateway behavior. `rejected` outcomes at R7/R10 are expected, normal Gateway behavior, never a
validity failure by themselves.

### 13-C. Run failure policy

Any 13-A/13-B gate failing on a measured run marks that run `INVALID` — artifacts preserved
(`invalid_reason` field populated in `result.json`, §21), root cause investigated, the same
(config, load, repeat-slot) is re-run in a fresh environment and both the invalid and retry
artifacts kept. Invalid runs never enter the 27-run canonical aggregate. Environment failures are
never counted as a Gateway performance failure; conversely, a normal admission `rejected` outcome
is a valid Gateway result, never grounds for invalidation by itself.

## 14. Formal execution scope — explicitly out of scope for this Unit

This Unit (6.5) freezes protocol and writes/statically-reviews harness and collector code. It does
**not** run the Canary and does **not** run any of the 27 Formal measured runs. Per the user's
explicit instruction:

- The **Canary** (§20) runs only after a separate, explicit user approval following this report.
- The **27-run Formal matrix** runs only after the Canary is confirmed PASS *and* a further
  separate user approval (or a prior explicit standing auto-progress approval) is given.

## 15. Counter plateau / stall detection

Reused directly from `scripts/run-phase2-native-formal-benchmark.sh`'s validated log-gap detector,
not a newly-invented threshold: parse `gateway.log`/`mock.log` timestamps, flag any gap exceeding
**20 seconds** anywhere the gap overlaps `[measurement_start, measurement_end]`. A gap does not by
itself auto-invalidate the run (Phase 2's own finding: a short-enough gap causes no outcome-cohort
harm, only a possible throughput-population timing shift) — it is written to
`counter-plateau-check.json` and flags the run for manual review of exactly which population (§12)
the gap could have affected before judging impact.

## 16. Postflight / drain window

After the k6 process exits (measurement + its own `gracefulStop` window), the harness does **not**
kill the Gateway/Mock processes immediately — it polls `gateway_admission_active`,
`gateway_active_streams`, `gateway_upstream_active` (and the implementation-specific gauges, §18)
every 1s. `drain_end` is recorded the moment all reach 0. Hard cap on the drain wait:
`CHAT_TOTAL_TIMEOUT_MS + 10000ms` (70s) — chosen as "the longest any single in-flight request could
legitimately still be running, plus a safety margin," per §32's instruction; in practice, since
Normal-workload service time is ~7.8s, drain is expected to complete within a few seconds of k6
exiting, and the 70s cap is a safety bound that should essentially never actually get hit for a
valid run. If the cap is reached with gauges still nonzero, the run is marked `INVALID`
(postflight-not-clean, §13-C) — not silently proceeded past.

Postflight gauge set (all must be 0, all three implementations, common):
`gateway_admission_active`, `gateway_active_streams`, `gateway_upstream_active`. Plus
implementation-specific (§18): P3-A `outbound_blocking_executor_active`; P3-A/B
`servlet_write_executor_active`, `servlet_write_executor_queue_depth`,
`servlet_write_stream_buffered_frames`; P3-B/C `reactor_netty_connection_provider_pending_connections`.
Mock LLM's own `mockllm_current_concurrency`/`mockllm_waiting_requests` (if exposed — confirmed
present per Unit 1/Phase 2 precedent) checked at 0 too.

## 17. Authoritative throughput

Primary: **§12-A completion throughput** (`completed_count_in_window / MEASUREMENT_SEC`) — the
number of requests that both arrived and physically completed inside the measurement window,
divided by the window's fixed duration. Arrival rate is never itself called "throughput" — at R10
the *arrival* rate is exactly 10 req/s by construction (k6's `constant-arrival-rate` executor
guarantees this), but the *completion* throughput is expected to be measurably lower (bounded by
`admission_ceiling / service_duration ≈ 50/7.8 ≈ 6.4 req/s`, per Unit 6's own rough-capacity
calculation) — the actual measured value, not the arithmetic estimate, is what gets reported.

## 18. Authoritative latency

Unit 5's decision stands, unchanged: cross-implementation Primary latency is **k6 client-side**
TTFC and total stream duration, **completed outcome only** (§19). Server-side
`gateway_first_chunk_relay_seconds`/`gateway_stream_duration_seconds` remain diagnostic-only — the
three implementations' server-side physical completion boundaries are not the same event (§10),
so they are never used for Primary cross-implementation comparison.

## 19. TTFC / stream-duration population

Only the `completed` outcome tag feeds Primary TTFC/stream-duration aggregation
(`client_ttfc_completed_seconds`, and `client_ttfc_seconds`/`client_stream_duration_seconds`
filtered to `outcome=completed` at aggregation time). `rejected` and `failed_*` populations are
never mixed into the completed-latency distribution; their own latencies (near-zero for `rejected`,
partial-stream for `failed_mid_stream`) are recorded separately as diagnostic only if needed.

## 20. Platform thread metric

Primary: `platform_threads_window_peak` = `max(jvm_threads_live_threads)` sampled **only within**
`[measurement_start, measurement_end]` — not the JVM's full process-lifetime peak, since (per the
user's explicit instruction) a warm-up-phase peak (e.g. transient thread creation during Reactor
Netty/connection-pool cold start) could otherwise contaminate the measurement-window number even
though the process is fresh per run. Computed from the same 1Hz Prometheus sample stream (§22) by
filtering samples to the window before taking `max()` — identical method across all three
implementations.

## 21. CPU metric

**Corrected by the Unit 7 Canary** (empirically found, not assumed): this stack's actual
`/actuator/prometheus` output (Boot 2.7.18 / Micrometer 1.9.17) does **not** expose
`process_cpu_seconds_total` — the cumulative-counter approach originally frozen here silently
produced no data. The only CPU signal actually present is `process_cpu_usage`, an instantaneous
**gauge** in `[0.0, 1.0]` (Micrometer `ProcessorMetrics` / `OperatingSystemMXBean#getProcessCpuLoad()`
semantics: `1.0` means the process used *all* available processors 100% of the time over the
recent sampling period — i.e. it is already a fraction of total multi-core capacity, not of one
core).

Primary, corrected formula: `cpu_avg_cores = mean(process_cpu_usage samples within
[measurement_start, measurement_end]) * available_processors` — converts the gauge's "fraction of
total capacity" into "average cores consumed," preserving the original unit-choice intent (average
CPU **cores**, not a percentage of one core or of total host capacity) using the metric that
actually exists. Peak instantaneous `process_cpu_usage` (as a raw fraction) is retained as a
secondary diagnostic figure alongside the mean.

## 22. RSS — sampling method and the Unit 6 Screening invalidation

**Unit 6 Screening's RSS numbers are INVALID** (harness PID-capture bug — the sampler tracked a
process that was not the actual Gateway JVM, confirmed by direct `ps` comparison; thread-count and
CPU numbers from that same Screening pass were unaffected, since those came from the live
`/actuator/prometheus` HTTP endpoint, independent of PID tracking). Screening RSS numbers must not
be used in any Final Report, portfolio document, or Formal load decision — none were, per
`docs/test-results/phase3/unit6-screening/SUMMARY.md` §6.

Formal's RSS sampler resolves the PID via `pgrep -f "<jar basename>"` **after** the Gateway's own
health check passes (the fix already applied in `scripts/run-phase3-native-screening.sh` following
that discovery, and carried into `scripts/run-phase3-native-formal-benchmark.sh` here) — not via
`$!` after an `env`/`nohup` chain, which was the actual source of the Screening bug. This is
verified again, explicitly, by the Formal Canary (§20/§23) before any of the 27 runs are trusted to
use it.

Sampling: native macOS `ps -o rss= -p <PID>`, 1Hz, for the full run (process start through
`drain_end`). Primary fields, all three preserved (delta alone is noise-prone, per the user's
instruction): `rss_measurement_start_bytes` (first sample at/after `measurement_start`),
`rss_measurement_peak_bytes` (max within `[measurement_start, measurement_end]`),
`rss_measurement_delta_bytes` (peak − start). Primary conclusions are drawn from start/peak
stability across repeats, not from the delta alone.

## 23. JVM heap / GC — secondary

`jvm_memory_used_bytes{area="heap"}` heap start/peak/delta within the measurement window (same
windowing rule as §20/§22), plus `jvm_gc_pause_seconds_count`/`jvm_gc_pause_seconds_sum` deltas
over the window (GC pause count and total pause time). Same Micrometer JVM metrics binder, all
three implementations — Secondary only, never conflated with RSS (heap is JVM-managed logical
memory; RSS is OS-level resident physical memory, and the two do not move together, especially
across implementations with different Reactor Netty/Servlet buffer allocation patterns).

## 24. Implementation-specific metrics

Structural-explanation metrics only, recorded where they exist, never fabricated as 0 for an
implementation that structurally lacks them (the field is `null` or the block is omitted, §21
schema):

- **P3-A only**: `outbound_blocking_executor_active`, pool size, largest-pool-size-in-window (a
  window-filtered max, same rule as §20), `blockingExecutorRejected` delta.
- **P3-A/B**: `servlet_write_executor_active`, `servlet_write_executor_queue_depth`,
  `servlet_write_stream_buffered_frames`, `servlet_write_executor_rejected` delta,
  `servlet_write_queue_wait_seconds`/`servlet_write_duration_seconds` window summary,
  `write_overflow` delta (§13-B: must be 0 in a valid Normal-workload run).
- **P3-B/C**: `reactor_netty_connection_provider_pending_connections` window max (must stay 0,
  §13-B), active/idle connection counts if exposed by the confirmed Reactor Netty metric names
  (`docs/decisions/phase3-metrics-contract.md`).
- **P3-C**: no additional implementation-specific block beyond what's already covered by §9/§10/
  §23 — P3-C has no Servlet write path and no blocking outbound executor by construction (the
  entire point of Experiment B), so there is nothing further to report here, not a gap.

## 25. Formal artifact directory and per-run schema

`docs/test-results/phase3/unit7-formal/<config>-<load>-run<N>/` (e.g. `p3a-r3-run1/`). Minimum
artifacts per run: `environment.json`, `timestamps.json`, `k6-summary.json`, `result.json`,
`gateway.log`, `mock.log`, `counter-plateau-check.json`, `rss-samples.csv`,
`prometheus-measurement-start.txt`, `prometheus-measurement-end.txt`,
`prometheus-postflight.txt`. No requirement to commit a full raw Prometheus TSDB — the raw
`/actuator/prometheus` text snapshots at the three key instants are the re-verifiable artifact.

### `timestamps.json`

```json
{
  "process_start": "...", "warmup_start": "...", "measurement_start": "...",
  "measurement_end": "...", "drain_end": "...", "process_stop": "..."
}
```

`measurement_start` is read back from the k6 summary export's `measurement_start_epoch_s` Gauge
(§11's single-clock-read rule) — the harness never independently recomputes it. `measurement_end`
is the nominal `measurement_start + MEASUREMENT_SEC` (computed, matching Phase 2's convention of a
nominal boundary rather than an after-the-fact observation). `warmup_start` is the harness's own
`date` read immediately before launching the k6 process (a few seconds of k6 startup latency before
the first iteration is expected and not itself a problem, since phase classification inside the k6
script is by k6's own internal `Date.now()`, not the shell's).

### `environment.json`

```json
{
  "run_id": "p3a-r3-run1", "config": "A", "load": "R3", "repeat": 1,
  "jdk": {"vendor": "Zulu", "version": "1.8.0_504", "build": "1.8.0_504-b01",
          "sha256_java_binary": "..."},
  "os": {"product": "macOS", "version": "...", "build": "...", "arch": "arm64"},
  "available_processors": 10, "power_source": "AC" ,
  "spring_boot": "2.7.18", "spring_framework": "5.3.31", "reactor_core": "3.4.34",
  "reactor_netty": "1.0.39", "micrometer": "1.9.17", "tomcat": "9.0.83 (P3-A/B only, null for P3-C)",
  "k6_version": "v1.8.0", "xk6_sse_version": "v0.1.11", "mock_llm_commit_or_note": "...",
  "gateway_config": {"...": "every env var actually passed to this run's Gateway process"},
  "blockhound": false, "docker": false, "slow_client": false
}
```

### `result.json` — top-level schema

```
valid, invalid_reason,
client_cohort, throughput, client_latency, platform_threads, cpu, native_rss, heap_gc,
admission, implementation_specific, postflight, environment
```

Missing implementation-specific fields for a config that structurally lacks them: `null` value or
the whole block omitted — never a fabricated `0`.

## 26. Repeat aggregation (performed after all 27 runs — not this Unit)

Per `(config, load)` cell, n=3: **median** is the Primary representative value; min/max recorded
alongside for range; CV (coefficient of variation) recorded as a dispersion check; mean recorded as
a raw secondary value, not the headline number. Primary aggregated metrics: completion throughput,
rejection rate, client TTFC p50/p95/p99, client stream duration p50/p95/p99,
`platform_threads_window_peak`, `cpu_avg_cores`, RSS start/peak/delta. Secondary: heap/GC,
implementation-specific resource metrics.

## 27. Pairwise comparison rule

Experiment A = P3-A vs. P3-B; Experiment B = P3-B vs. P3-C. Per load point: absolute difference and
relative percentage difference on each Primary metric. **No significance testing language** — with
n=3 per cell, "statistically significant" is not claimed anywhere in the Formal report; differences
are reported as measured magnitudes with their CV, and readers draw their own conclusions about
whether a difference is large relative to its own run-to-run variability.

## 28. Formal hypotheses (H3, final wording — neutral, testable)

- **H3-a**: P3-B's `platform_threads_window_peak` is lower than P3-A's at the same load.
- **H3-b**: P3-A and P3-B have similar completion/rejection capacity at the same load, under the
  same (now-unified) admission/downstream conditions.
- **H3-c**: P3-C's `platform_threads_window_peak` is lower than P3-B's at the same load.
- **H3-d**: P3-B and P3-C have similar completion/rejection capacity at the same load, under the
  same admission/downstream conditions.
- **H3-e**: Client TTFC and stream duration on the Normal workload do not differ substantially
  across the three implementations at a given load. (Reworded from the original directional
  draft — this is a neutral "expect similarity, report the actual measured difference" hypothesis,
  not a prediction that they must match.)
- **H3-f**: A change in execution model (blocking-Servlet -> non-blocking-Servlet -> reactive)
  does not necessarily reduce CPU or RSS — each is reported as measured, not assumed to move in the
  "obviously better" direction just because the execution model changed.

Slow Client and BlockHound diagnostic findings (Unit 5.5) are not mixed into these Formal
hypotheses — they remain separate, documented risk/design findings (§10 above).

## 29. Canary (§14 — not yet executed)

One run, condition: **P3-A, R3, `canary` label** (not `run1` — excluded from the 27-run canonical
matrix numbering even though it uses the exact same harness path and config). Purpose: validate the
*pipeline*, not collect a performance result.

### 29-A. Canary PASS criteria

- `GATEWAY_PID` (resolved via `pgrep -f`) matches the actual running java process's command line
  (jar basename) — verified by the harness itself, and independently spot-checked by the operator
  via `ps` against the recorded PID, exactly as this conversation's Unit 6 investigation did by
  hand.
- Native RSS samples are non-zero, plausible for a JVM (order of 100s of MB, not the ~1.5MB the
  Unit 6 Screening bug produced), and change sensibly between `measurement_start` and
  `measurement_peak`.
- `k6 dropped_iterations == 0`.
- Client cohort invariant (§12) holds exactly.
- R3 rejection count is 0 or within a reasonable range of Screening's R3 result (0/76) — a sanity
  check against the harness producing nonsense, not a hard performance gate.
- Client TTFC (completed) falls in the expected ~1.0s range (`firstChunkDelayMs=1000`); stream
  duration (completed) falls in the expected ~7.8–7.9s range.
- No plateau/log-gap over 20s; zero unexpected errors; postflight clean (§16).

A PID mismatch, or any of the above failing, blocks the 27-run matrix from starting — the harness
is fixed and the Canary re-run, not skipped.

## 30. Unit 6.5 deliverables (this Unit)

- `docs/test-plan/phase3-formal-protocol.md` (this document).
- `scripts/phase3-formal-k6.js` — continuous warm-up→measurement k6 script, Phase-3-specific
  (Phase 2's `03-constant-arrival-rate-continuous.js` read for technique, not modified/imported).
- `scripts/run-phase3-native-formal-benchmark.sh` — single-run engine (fresh processes, continuous
  k6 invocation, RSS sampler with verified PID resolution, Prometheus snapshots at the three key
  instants, plateau/stall check, drain, postflight, invokes the collector).
- `scripts/run-phase3-native-formal-matrix.sh` — the frozen 27-run order (§4) as a literal array,
  calling the single-run engine; **not executed this Unit**.
- `scripts/collect_phase3_formal_result.py` — parses each run's raw artifacts into `result.json`
  per the §25 schema.
- `scripts/aggregate_phase3_formal.py` — deferred; created only after the 27 runs actually
  complete, per the user's explicit instruction that this is acceptable to defer.

## 31. Unit 6.5 completion checklist

All frozen above: R3/R7/R10 (§2); 3×3×3=27 matrix (§3); run order (§4); native environment (§6);
config values (§8); Normal Mock workload (§8); admission semantics (§9/§10, carried over from
Unit 6, not re-litigated); warm-up duration (§11); measurement duration (§11); drain policy (§16);
client cohort (§12); throughput definition (§17); client latency authority (§18/§19); platform-
thread definition (§20); CPU definition (§21); RSS definition (§22); heap/GC (§23);
implementation-specific metrics (§24); validity gates (§13); dropped-iteration rule (§13-B);
plateau/stall rule (§15); postflight (§16); artifact schema (§25); result schema (§25);
aggregation rule (§26, to be executed after 27 runs); hypotheses (§28); Canary protocol (§29);
invalid/retry policy (§13-C). **All 28 items frozen — Unit 6.5 protocol-freeze scope complete.**
