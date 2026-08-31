# Phase 4 Closed-model Harness Protocol

Status: Accepted (Phase 4 Unit 4 scope — measurement pipeline verified at N=20 only; this is NOT a
scalability result, see `docs/test-results/phase4/unit4-closed-harness/SUMMARY.md`)
Date: 2026-08-27

This document freezes the closed-model measurement pipeline `scripts/run-phase4-closed-benchmark.sh`
+ `scripts/collect_phase4_closed_result.py` will use unmodified through Unit 5 screening — the
values below are frozen and must not change after Unit 5 screening starts (same principle as every
other Phase 4 numeric freeze).

## 1. Harness architecture

Per invocation (`run-phase4-closed-benchmark.sh <model> <N> <run_label>`), exactly one model:

```
fresh Mock LLM (unlimited) → fresh Gateway JVM → fresh Prometheus (fresh TSDB)
  → k6 closed wave (internal sequential prewarm + concurrent wave)
  → drain → metric snapshot/range-query → postflight → teardown → collector
```

No process/state is reused across models or across runs — every run starts all three processes
fresh (docs/test-plan/phase4-design.md Unit 4 brief section 10).

## 2. Prewarm — freeze: 5 sequential requests

`PREWARM_COUNT=5`, executed inside the k6 script's `setup()` (single-threaded, sequential —
`sse.open()` blocks until that stream's terminal signal before the next prewarm call starts).
Confirmed working in all three Unit 4 smokes: Mock's own `mockllm_completed_requests_total` read
25 (5 prewarm + 20 wave) while the client-cohort wave count stayed exactly 20 in every run — prewarm
traffic is real (hits the Gateway/Mock, warms JIT/connections/framework) but is structurally
excluded from every wave metric (it's counted by a different k6 lifecycle function, `setup()` vs
`default()`, not by a runtime flag that could leak).

## 3. Wave-start / concurrency semantics

k6 `shared-iterations` executor (`vus: N, iterations: N`) — all N VUs are launched immediately.
**Verified, not assumed**: `client_wave_start_epoch_ms` (new Trend metric,
`load-test-k6/scenarios/04-phase4-closed-wave.js`) recorded a `max - min` start spread of **1ms
(M1), 2ms (M2), 1ms (M3)** at N=20 — the wave is genuinely near-simultaneous at this scale.

**Acceptable start-spread threshold (freeze)**: `spread_ms < CHUNK_INTERVAL_MS (200ms)`. Rationale:
this is a structural bound, not a number tuned to fit the observed 1-2ms result — 200ms is the
workload's own finest event granularity (inter-chunk interval); a start spread below that cannot
meaningfully distort what "concurrent" means for this workload, and one at or above it would
start to blur distinct chunk-arrival timings across VUs. This threshold is carried into Unit 5
unchanged; if higher-N screening shows spread approaching it, that is itself a harness finding to
report, not a threshold to loosen after the fact.

## 4. Concurrent population authority

**Client-side** (`started`/`client_wave_start_epoch_ms`) is authoritative for "how many streams
were attempted, how tightly clustered." **Server-side Prometheus per-model peak active metric**
(`executor_active` for M1, `virtual_tasks_active` for M2,
`reactor_netty_connection_provider_active_connections` for M3) is used as the concurrent
**in-flight population** authority — at N=20 this matched the client's N exactly in all three
models (peak=20.0 in every case), so no discrepancy needed resolving this Unit. At higher N in Unit
5, M1's `executor_active` can legitimately show <=50 (worker cap) while `executor_queue_depth`
carries the rest — both are collected, and `client_stream_completed_total`/`_failed_total` remain
the reliability authority regardless (§5).

## 5. Client cohort — target vs actual_started, exactly-once accounting (Unit 4.6 revision)

**`target_concurrency` (what k6 was configured to attempt) and `actual_started` (what it actually
managed to start) are never assumed equal.** Earlier collector drafts used `N` directly as the
invariant source — this is wrong: at higher screening N, the load generator itself can fail to
spin up every VU (a real `LOADGEN_LIMIT`), and treating `N` as if it were the actual population
would silently misattribute a load-generator shortfall to the Gateway.

`actual_started` is read from an **explicit Counter**
(`client_actual_started_total`, incremented as literally the first line of every iteration,
`load-test-k6/scenarios/04-phase4-closed-wave.js`) — not inferred from a Trend's incidental
`.count` field.

```
target_reached = (actual_started == target_concurrency)
terminal_sum = completed + rejected + failed_mid_stream + failed_no_event
invariant_ok = (terminal_sum == actual_started)     -- NOT target_concurrency
```

Both `target_reached` and `invariant_ok` are hard validity gates (§13). Verified in all three Unit
4.6 real runs: `actual_started == target_concurrency == 20` in every case (no load-generator
shortfall observed at this scale) — `target_reached` and `invariant_ok` both `true`. Synthetic
fixture proof (`scripts/test_phase4_collector_synthetic.py`): `actual_started=80` against
`target=100` correctly produces `target_reached=false` → `valid=false`, even though the cohort's
own internal accounting (`terminal_sum==actual_started`, 80==80) is otherwise self-consistent —
self-consistent accounting of a short population is still not a valid measurement of the *planned*
concurrency.

Prometheus's own `gateway_requests_total` sum is NOT forced to exact-equal the client cohort
(docs/test-plan/phase4-design.md Unit 4 brief section 15) — client cohort remains the single
authoritative source for reliability/outcome-count judgment; server-side outcome metrics are used
for *classification evidence* only (§10 below), not as a second population-counting source.

## 5a. Completed-only SLO population (Unit 4.6)

The frozen Phase 4 SLO (`docs/decisions/phase4-scalability-definition.md` §2: TTFC p95<=2.0s,
stream duration p95<=10.0s) applies to **completed requests only**. Mixing rejected/failed
requests' near-instant "duration" into the same latency population would silently make the
aggregate p95 look *better* than the real completed-request experience — a rejection returns in
~10ms, and enough rejections dragged into an aggregate trend can mask real completed-request
degradation.

`load-test-k6/scenarios/04-phase4-closed-wave.js` records two additional Trends,
`client_completed_ttfc_seconds` / `client_completed_stream_duration_seconds`, populated **only**
when `outcome === 'completed'` (the pre-existing `client_ttfc_seconds`/
`client_stream_duration_seconds` remain as ALL-outcome diagnostic trends, never SLO-authoritative).
`collect_phase4_closed_result.py`'s `client_latency` block reads `completed_ttfc_p95_s`/
`completed_duration_p95_s` from these completed-only trends as the sole SLO-authoritative fields.

**`completed == 0` case**: `completed_ttfc_p95_s`/`completed_duration_p95_s` are recorded as
`null` — never fabricated as `0` or any placeholder value. A `completed=0` run is not a validity
failure by itself (it's very likely a genuine RED reliability result, §11) — `null` latency simply
means "no completed population to measure latency over," which the classifier (§11) already knows
not to require for a RED verdict.

Synthetic proof: a 50-completed/50-rejected N=100 fixture yields `completed_duration_p95_s≈8.0s`
(the completed population's own true value), completely unaffected by the 50 near-instant rejected
"durations" that would otherwise have dragged an aggregate value down.

## 6. Prometheus scrape interval — freeze: 1s

`monitoring/prometheus/prometheus-phase4.yml`: `scrape_interval: 1s`, `scrape_timeout: 900ms`.
Chosen over Phase 2/3's 5s (`docs/decisions/phase2-formal-native-macos-environment.md` precedent)
because a Closed wave's canonical stream duration is ~7.8-7.9s — a 5s scrape can miss the single
relevant peak window for a short single-wave measurement (design brief section 26's own
reasoning). Verified this Unit: three back-to-back 1s-interval runs (Gateway + Mock, 2 scrape
targets) produced 44-47 samples per ~45-50s run with no observable Prometheus overhead issue
(`prometheus.log`, clean startup/scrape, no dropped-scrape warnings). **Frozen for Unit 5 — not
revisited after screening starts.**

## 7. Resource metric policies (Unit 3 formulas, verified live this Unit)

- **RSS**: `ps -o rss= -p <gateway_pid>`, 1Hz (`rss-samples.csv`) — verified non-zero,
  monotonic-ish, sensible values in all three runs (start~236-243MB, peak~256-262MB).
- **FD**: Gateway's own Micrometer `process_files_open_files` (Primary, per Unit 3 policy) —
  verified present and sensible in all three models this Unit (peak 59/61/86 respectively; `lsof`
  cross-check not run this Unit — not needed, the Micrometer gauge is directly confirmed working).
- **CPU — formula source-confirmed and frozen (Unit 4.5)**: bytecode inspection of the exact
  `micrometer-core-1.17.0.jar` this project resolves (`javap -p -c` on
  `io/micrometer/core/instrument/binder/system/ProcessorMetrics.class`) confirms `process.cpu.usage`
  is registered via reflection over `getProcessCpuLoad` — i.e.
  `com.sun.management.OperatingSystemMXBean.getProcessCpuLoad()`, whose JDK-documented semantic
  (stable Java SE API since JDK 7) is: a value in `[0.0, 1.0]` where `1.0` means **all available
  CPUs** were saturated by this process 100% of the time — i.e. the value is *already* a fraction
  of total host capacity, not per-core. Micrometer's own embedded description string (also read
  directly from the class file) confirms this: `"The \"recent cpu usage\" for the Java Virtual
  Machine process"`. This means the correct conversion to "cores used" is a simple multiply by the
  total core count, not a rate/derivative calculation (Phase 1/2's `process_cpu_seconds_total`
  formula, a cumulative counter needing `rate()`, does not apply here — different metric family
  entirely). **Frozen formula**: `avg_cores = mean(process_cpu_usage samples in run window) *
  availableProcessors`. All Unit 4 raw `process_cpu_usage` samples (135 samples across the three
  models) were independently confirmed bounded in `[0.0, 1.0]` with no negative/NaN/>1 values,
  corroborating the source-derived semantic against real data. `collect_phase4_closed_result.py`
  computes both `avg_cores_estimate` (primary, mean-based, per this freeze) and a secondary
  `peak_cores_estimate_diagnostic` (peak-based, useful only for saturation-moment diagnostics, not
  the frozen average). N=20 Unit 4 numbers (~0.05-0.06 avg-cores across the three models) are
  **not** used as a performance comparison — this Unit only confirms the formula is source-correct
  and computable, not that any Unit 4 N=20 number itself means anything comparative.
- **Platform threads**: `jvm_threads_live_threads` peak — confirmed present in all three (M1=116,
  M2=106, M3=30 at N=20). **This difference is explicitly NOT a performance conclusion this Unit**
  (docs/test-plan/phase4-design.md Unit 4 brief section 38) — it reflects each server model's
  baseline thread footprint (Tomcat's default NIO/exec pool vs Reactor Netty's lean event-loop
  group) at trivial load, not a scalability signal.

## 8. Postflight — automated in the collector

```
gateway_active_requests == 0
gateway_upstream_active == 0
mockllm_active_requests == 0
(M1 only) executor_active == 0, executor_queue_depth == 0
(M2 only) virtual_tasks_active == 0
```

Confirmed clean in all three Unit 4 runs.

## 9. Plateau / stall policy — automated, frozen (Unit 4.5)

**Why Phase 2's 20s counter-plateau rule does not transfer as-is**: investigation of actual Unit 4
raw evidence found that in Phase 4's Closed single-wave model, *every* relevant counter/gauge has
long, entirely normal plateaus as part of its expected shape — `gateway_active_requests`/
`mockllm_active_requests` jump to N near wave-start and sit flat at N for the whole ~7.8-7.9s
stream duration; terminal/completed counters sit at 0 for ~7.8-7.9s then jump to N
near-simultaneously. A naive "any counter flat for N seconds = stall" rule would misfire on every
single healthy run. Worse, `gateway.log` carries almost no lines during the active window **by
design** (docs/test-plan/phase4-design.md Unit 2 section 44, no per-request hot-path logging) —
confirmed by inspecting the actual Unit 4 `gateway.log` files (24 lines total, all
startup/shutdown) — so a Gateway log-gap check is not viable at all.

**Adopted design** (`scripts/check_phase4_stall.py`): two independent heartbeat signals, plus a
client-side correlator:

1. **Gateway scrape-heartbeat gap**: the timestamps already present in any collected Prometheus
   range-query JSON (1s scrape interval) are themselves a heartbeat — a gap bigger than a few
   scrape intervals means Prometheus failed to scrape the Gateway's `/actuator/prometheus`, i.e.
   the process was unresponsive to HTTP. **Threshold: 5s.**
2. **Mock application-log gap**: `mock.log`'s timestamped lines (`stream requested`/`completed`,
   from Mock's own `log()` calls — untimestamped uvicorn access-log lines are ignored) fire once
   per request lifecycle event. The natural gap between the "requested" burst and the "completed"
   burst is ~7.8-7.9s (confirmed from real Unit 4 data: 7.848-7.852s across all three models) —
   this repeats once per sequential prewarm request and once for the wave. **Threshold: 15s**
   (~2x margin over the confirmed ~7.85s natural maximum, well under `CHAT_TOTAL_TIMEOUT_MS=60s`).
3. **Client-side duration anomaly**: `client_stream_duration_seconds` max > 20s (~2.5x the ~7.8-7.9s
   baseline) — mirrors the real anomaly found in Unit 3 (`docs/test-results/phase4/
   unit3-control-calibration/SUMMARY.md` §4, R=20 measurement artifact).

**Correlation rule** (frozen):
```
stall_detected = (gateway_gap_exceeds AND mock_gap_exceeds)
                  OR (client_duration_anomaly AND (gateway_gap_exceeds OR mock_gap_exceeds))
```
A single, uncorrelated signal (e.g. one isolated slow scrape) is recorded as advisory evidence in
`stall-check.json` but does **not** flip `stall_detected` — avoids false positives from a lone blip
while still catching a real environment-wide freeze. Verified with three test classes (`scripts/
check_phase4_stall.py`, exercised via both the three real Unit 4 raw artifacts and two synthetic
fixtures — see `docs/test-results/phase4/unit4-closed-harness/SUMMARY.md` §Stall checker
verification for the actual pass/fail evidence): clean real runs → false, a correlated synthetic
stall (28s Mock gap + 29s Gateway gap + 31.2s client duration) → true, an isolated single-signal
synthetic case (9s Gateway-only gap, everything else normal) → false.

`stall_detected` is wired into `collect_phase4_closed_result.py`'s hard validity gate
(`environment_stall_false`) — a detected stall invalidates the run. **Frozen — not revisited after
Unit 5 screening starts.**

## 10. PID / process verification

Every run captures `pid-verification.txt`: the Gateway's actual PID, its actual `ps -o command=`
output, and a substring match against the expected jar filename for that model. A mismatch is a
hard `PID_MATCH=false` → run marked invalid. Confirmed `PID_MATCH=true` in all three Unit 4 runs.

## 11. Result schema

`collect_phase4_closed_result.py` writes `result.json` (full evidence) and `validity.json`
(pass/fail + per-check booleans) per run — Closed-specific schema (`client_cohort`,
`client_latency`, `wave_start_spread`, `platform_threads`, `cpu`, `rss`, `fd`, `heap`,
`common_gateway`, `implementation_specific`, `mock`, `postflight_clean`, `timestamps`,
`environment`). No arrival-rate/throughput field is forced in as Primary — this is a Closed
harness, not an Open one (docs/test-plan/phase4-design.md Unit 4 brief section 35).

## 12. Frozen config per model (verified as actually-effective this Unit, not just source-frozen)

| | M1 | M2 | M3 |
|---|---|---|---|
| Worker/queue | `PT_WORKER_COUNT=50`, `PT_QUEUE_CAPACITY=500`, `ArrayBlockingQueue(fairness=false)`, `AbortPolicy` | n/a (VirtualThreadPerTaskExecutor, no admission) | n/a |
| Write path | `SERVLET_WRITE_POOL_SIZE=64`, `SERVLET_WRITE_QUEUE_CAPACITY=20000`, `SERVLET_PER_STREAM_BUFFER_CAPACITY=8` | same (byte-identical config) | n/a |
| Connection pool | n/a | n/a | `WEBCLIENT_MAX_CONNECTIONS=800`, `WEBCLIENT_PENDING_ACQUIRE_MAX_COUNT=800` |
| `CHAT_TOTAL_TIMEOUT_MS` | 60000 | 60000 | 60000 |

Effective values confirmed via `environment.json` per run (not just assumed from source) — matches
the Unit 2 freeze exactly, no drift.

## 13. Measurement validity vs model outcome — frozen distinction (Unit 4.5, amended Unit 4.6)

**This is the single most important rule for interpreting Unit 5 screening results correctly.**

A run's `valid` flag answers *"can this measurement be trusted"* — it never answers *"did the
model perform well."* An M1 request legitimately rejected by `AbortPolicy` under real saturation,
or any model genuinely timing out under real load, is **not** a harness failure — it is exactly the
kind of result Phase 4 exists to find. Conflating the two would make it impossible to ever observe
a model's real RED boundary without the harness itself reporting "invalid."

**INVALID** (harness/environment integrity broken — `collect_phase4_closed_result.py`'s
`validity` block):
- `target_reached == false` (§5: `actual_started != target_concurrency` — a load-generator
  shortfall, not a Gateway result)
- cohort invariant broken (`terminal_sum != actual_started`), `dropped_iterations != 0`
- `k6_exit_code != 0`, PID mismatch
- RSS/FD/CPU/Prometheus sampling data missing or unparsable
- `wave_start_spread >= 200ms` (§3)
- `gateway_requests_total{outcome="internal_error"} != 0` (a genuine application bug, not a model
  outcome)
- `servlet_write_overflow_total != 0` for M1/M2 (write-path hidden limiter or bug at a load the
  write-path config was never sized to fail at — §14 amendment)
- M3 `reactor_netty_connection_provider_pending_connections > 0` while `N < 800`
  (`WEBCLIENT_MAX_CONNECTIONS`) — unexpected connection-pool contention, not a modeled outcome
- postflight not clean, `environment_stall_true` (§9)
- `prewarm_contamination_ok == false` (§15 — the prewarm-always-completes assumption was violated)
- `N > SAFE_CLOSED_MAX (640)` — control-range violation (`docs/decisions/
  phase4-scalability-definition.md` §16)

**VALID (and may still show a RED/AMBER model outcome)**:
- M1 `AbortPolicy` rejections (`client_rejected_total > 0`) — `MODEL_REJECTION`
- Any model's requests timing out under genuine saturation
  (`client_failed_no_event_total`/`client_failed_mid_stream_total > 0` with no
  `internal_error`/`write_overflow`/stall/PID-mismatch alongside it) — `MODEL_TIMEOUT`/
  `MODEL_SATURATION`
- TTFC/stream-duration SLO failure (`docs/decisions/phase4-scalability-definition.md` §2) — that
  IS an AMBER/RED classification input, not an invalidity signal
- Growing queue depth (M1 `executor_queue_depth`), high CPU/RSS — resource-ceiling evidence, not
  invalidity

`result.json`'s `model_outcome` block is explicitly separated from the `validity` block for this
reason — Unit 5's screening loop reads `valid` to decide whether a run counts at all, and separately
reads `model_outcome`/`client_latency`/resource fields to classify GREEN/AMBER/RED
(`docs/decisions/phase4-scalability-definition.md` §7) for a *valid* run.

## 14. Harness freeze declaration — FINAL (Unit 4.6)

As of Unit 4.6, the following are **FINAL UNIT 5 SCREENING FREEZE** and must not change after
screening starts (only a STOP → contract-amendment-proposal → user-approval → regression →
restart cycle, per `docs/test-plan/phase4-design.md` Unit 4 brief section 40, may alter them):

- `scripts/run-phase4-closed-benchmark.sh`
- `scripts/collect_phase4_closed_result.py`
- `scripts/check_phase4_stall.py`
- `scripts/test_phase4_collector_synthetic.py` (regression suite — re-run if any of the above are
  ever amended under the STOP/amendment cycle)
- `load-test-k6/scenarios/04-phase4-closed-wave.js`
- `monitoring/prometheus/prometheus-phase4.yml`
- Every numeric freeze in this document (§2 prewarm=5, §3 start-spread<200ms, §5 target-vs-actual/
  cohort invariant semantics, §5a completed-only SLO population, §6 scrape=1s, §7 CPU formula, §9
  stall thresholds, §12 per-model config, §15 prewarm-correction assumption, §16 classification
  rules)

Unit 5 Closed Screening is scoped to `N <= SAFE_CLOSED_MAX (640)`
(`docs/test-results/phase4/unit3-control-calibration/closed-control-summary.json`); the
control-censored policy (`docs/decisions/phase4-scalability-definition.md` §16) applies if any
model is still GREEN at N=640.

## 15. Server-side outcome evidence — before/after delta, prewarm-corrected (Unit 4.6)

`gateway_requests_total{outcome="..."}` (all 7 label values: `completed`, `rejected`, `timeout`,
`upstream_error`, `client_disconnect`, `internal_error`, `write_overflow` — confirmed exported set,
`docs/decisions/phase4-metrics-contract.md` §3) is read **twice** per run: once immediately after
the Gateway health check passes (`gateway-metrics-before-wave.txt`, i.e. before prewarm even
starts — zero traffic processed yet) and once after the wave drains
(`gateway-metrics-final.txt`). The delta between these two snapshots covers **both** the 5 prewarm
requests and the N-request wave combined.

**Prewarm-correction assumption** (documented explicitly, per the brief's requirement): prewarm is
assumed to always terminate as `completed` in a healthy run — it uses the identical Normal
workload and identical Gateway path as the wave, just sequential instead of concurrent, so there is
no structural reason for it to produce any other outcome. Under this assumption:

```
server_wave_completed_estimate = delta['completed'] - PREWARM_COUNT
prewarm_contamination_ok = (round(server_wave_completed_estimate) == client's completed count)
```

If this equality fails, the assumption was violated (most likely: a prewarm request itself
failed/timed out) — the run is marked **invalid** (`prewarm_contamination_ok` in the validity
gate, §13) rather than silently mis-attributing a prewarm failure's outcome to the wave. When the
assumption holds (verified in all three Unit 4.6 real runs — `prewarm_contamination_ok=true`,
`server_wave_completed_estimate` exactly matched the client's completed count of 20 in every
case), all non-`completed` outcome deltas are attributed wholly to the wave (prewarm structurally
cannot have contributed to them under the same assumption) and used as-is for classification
evidence (§16).

## 16. GREEN / AMBER / RED classification — mechanically computable (Unit 4.6)

```
reliability_pass = (completed == actual_started) AND target_reached
slo_pass         = completed_ttfc_p95_s <= 2.0 AND completed_duration_p95_s <= 10.0   (only meaningful if completed > 0)

GREEN  = reliability_pass AND slo_pass
AMBER  = reliability_pass AND NOT slo_pass
RED    = NOT reliability_pass   (only reachable for a run that is still `valid` -- see §13:
                                  environment/control invalidity is INVALID, never RED)
```

A `RED` run is further sub-classified using the server-outcome evidence from §15 — **conservatively,
never asserting a cause the evidence doesn't support** (docs/test-plan/phase4-design.md Unit 4.6
brief sections 13/15/24):

| Server evidence | Classification |
|---|---|
| `rejected > 0` | `MODEL_REJECTION` |
| `timeout > 0` (and no rejection) | `MODEL_TIMEOUT` |
| `client_disconnect > 0` (and no rejection/timeout) | `UNEXPECTED_CLIENT_DISCONNECT` — Normal Closed workload has no intentional disconnect; this requires investigation, never auto-classified as ordinary model behavior |
| `upstream_error > 0` (and none of the above) | `UPSTREAM_ERROR_UNCONFIRMED_CAUSE` — could be `DOWNSTREAM_LIMIT`, a control problem, or genuine saturation; Mock/control corroboration is required before asserting a specific taxonomy label, which this Unit's schema does not yet automate (left for Unit 5's screening loop to cross-check against Mock's own metrics when it actually occurs) |
| `internal_error > 0` | `INTERNAL_ERROR_BUG` — already caught by the validity gate (`internal_error_eq_0`), so a *valid* run should never reach this branch; present only as a defensive fallback |
| none of the above, but client-side non-completed outcomes exist | `UNCLASSIFIED_MODEL_FAILURE` — explicitly not forced into `MODEL_TIMEOUT` or any other label without evidence |

Verified via `scripts/test_phase4_collector_synthetic.py` (24 checks, all passing): GREEN (real
N=20 × 3 + synthetic), AMBER (synthetic: reliability maintained, TTFC p95=2.5s > 2.0s threshold),
`MODEL_REJECTION` (synthetic, both the 50/50 split and the 550/50 split), `MODEL_TIMEOUT`
(synthetic, server `timeout` evidence present), `UNCLASSIFIED_MODEL_FAILURE` (synthetic, client
failure with zero server-side outcome evidence — proves the classifier does NOT default to
`MODEL_TIMEOUT` or any other specific label without support).

## 17. MSC / MRC bracket semantics — supported by this schema

```
MSC bracket: highest N with color=GREEN  <->  lowest N with color != GREEN
MRC bracket: highest N with color in {GREEN, AMBER}  <->  lowest N with color=RED
```

AMBER (SLO violated but reliability maintained) can fail MSC while still passing MRC — the schema
computes both `reliability_pass` and `slo_pass` as independent booleans specifically so Unit 5's
screening loop can derive both brackets mechanically from a sequence of `result.json` files
without additional judgment calls at screening time. Environment/control-invalid runs
(`valid=false`) are excluded from both bracket calculations entirely — they answer neither
question.
