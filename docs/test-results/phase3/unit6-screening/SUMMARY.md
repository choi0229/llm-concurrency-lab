# Unit 6 — Screening Summary

Status: Screening only (not Formal). Runs under
`docs/test-results/phase3/unit6-admission-regression/` (functional regression) and
`docs/test-results/phase3/unit6-screening/<CFG>/<label>/` (this doc) — raw k6/Prometheus/RSS
samples preserved per run, no repeats, no full JFR/heap artifact retention (Screening-level rigor
per `docs/test-plan/phase3-design.md` §16).
Date: 2026-08-23

Prerequisite (PASS, see `docs/decisions/phase3-admission-semantics-unification.md`): Admission
Semantics Audit — all three implementations now release the admission permit at upstream-call
lifetime boundaries, not response-lifecycle boundaries. Functional regression (F1–F5) re-verified
clean on all three after the code change (below).

## 1. Config freeze (actual code/defaults, not guessed)

Common: `MOCK_LLM_BASE_URL=http://127.0.0.1:8000`, `CHAT_ADMISSION_LIMIT=50`,
`CHAT_TOTAL_TIMEOUT_MS=60000`.

| Axis | Value | Source |
|---|---|---|
| P3-A `CHAT_BLOCKING_POOL_SIZE` | 50 (= admission ceiling) | `ExecutorConfig.java` default already falls back to `admissionGate.limit()`; set explicitly |
| P3-A/B `SERVLET_WRITE_POOL_SIZE` | 8 | code default (`ExecutorConfig.SERVLET_WRITE_POOL_SIZE_DEFAULT`) |
| P3-A/B `SERVLET_WRITE_QUEUE_CAPACITY` | 64 | code default |
| P3-A/B `SERVLET_PER_STREAM_BUFFER_CAPACITY` | 32 | code default (`ChatController.PER_STREAM_BUFFER_CAPACITY_DEFAULT`) |
| P3-B/C `WEBCLIENT_MAX_CONNECTIONS` | 50 (= admission ceiling) | `WebClientConfig.java` default already falls back to `admissionGate.limit()`; set explicitly |
| P3-B/C `WEBCLIENT_PENDING_ACQUIRE_MAX_COUNT` | 1 | code default |
| P3-B/C `WEBCLIENT_PENDING_ACQUIRE_TIMEOUT_MS` | 1000 | code default |
| P3-B/C `WEBCLIENT_CONNECT_TIMEOUT_MS` | 3000 | code default |
| P3-B/C `WEBCLIENT_LOOP_THREADS` | unset (= available processors) | code default; identical on both since same machine |

## 2. Normal Mock workload (frozen, reused from Phase 2's Formal workload)

```
firstChunkDelayMs = 1000
chunkIntervalMs   = 200
chunkCount        = 35
chunkSizeBytes    = 64
```

Source: `docs/decisions/phase2-formal-linux-environment.md` §5, and
`load-test-k6/scenarios/02-constant-arrival-rate.js`'s own defaults (Phase 2 asset, read not
modified). Measured client `client_stream_duration_seconds` in every Screening run: **avg
5.1–7.9s depending on rejection mix; completed-only population consistently 7.82–7.90s** — matches
the target "~7.8–7.9s" band exactly.

## 3. Rough capacity calculation

```
admission ceiling (upstream-lifetime, post-unification) = 50
Normal upstream service duration                         ≈ 7.8s
rough capacity                                            = 50 / 7.8 ≈ 6.4 req/s
```

Per `docs/decisions/phase3-admission-semantics-unification.md`, this calculation now uses
**upstream service duration**, not client total stream duration — consistent with the new
canonical admission definition.

## 4. Warm-up

5 sequential real requests (small distinct workload: `firstChunkDelayMs=50, chunkIntervalMs=50,
chunkCount=3, chunkSizeBytes=16`, ~200ms each) issued via `curl` immediately after the Gateway
passes its health check and before any sampler/k6 starts. Identical procedure for all three
implementations, on every (config × rate) run. Not included in the measured k6 window.

## 5. Rate sweep matrix

Open-model `constant-arrival-rate`, 25s pilot duration, `gracefulStop=90s`,
`preAllocatedVUs = RATE*20+50`, `maxVUs = RATE*60+150` (sized generously above expected concurrent
iterations so the load generator itself is never the bottleneck).

Rates tested: **R3, R6, R7, R8, R10** (req/s) — bracketing the ≈6.4 req/s rough-capacity estimate
from below, at, and above. Same rate set, same workload, same warm-up, all three configs.

## 6. Results

| Rate | P3-A completed/rejected | P3-B completed/rejected | P3-C completed/rejected |
|---|---|---|---|
| R3 | 76 / 0 | 76 / 0 | 75 / 0 |
| R6 | 151 / 0 | 150 / 0 | 151 / 0 |
| R7 | 161 / 15 | 160 / 15 | 160 / 15 |
| R8 | 162 / 39 | 161 / 39 | 161 / 39 |
| R10 | 163 / 87 | 164 / 87 | 163 / 87 |

**First rejection point**: between R6 (0 rejected in all three) and R7 (~8.5% rejected in all
three) — consistent across all three implementations, and consistent with the ≈6.4 req/s rough
capacity estimate. No `failed_mid_stream`/`failed_no_event`/`dropped_iterations` observed in any
run — every non-completed outcome was a clean `503 rejected`, never a load-generator or Gateway
failure.

**Client TTFC (completed population)**: 1.002–1.026s across every run at every rate for all three
— essentially flat, matching `firstChunkDelayMs=1000` almost exactly regardless of load level.
Admission is a hard gate, not a queue, so an admitted request's service is unaffected by how many
other requests were rejected around it — this is the expected, correct signature of that design,
not a surprising finding.

**Client stream duration (completed population)**: 7.82–7.90s at every rate for all three,
matching the frozen Normal workload's target duration — no load-dependent degradation observed
within the tested range (expected, since a `tryAcquire()`-based admission gate does not let
admitted requests experience queueing delay).

### Platform-thread curve (peak `jvm_threads_live_threads` per run, via live `/actuator/prometheus`)

| Rate | P3-A | P3-B | P3-C |
|---|---|---|---|
| R3 | 84 | 53 | 37 |
| R6 | 90 | 58 | 37 |
| R8 | 91 | 60 | 37 |
| R10 | 90 | 63 | 37 |

Clear, expected qualitative signal for both experiments: **P3-A carries the most platform threads
and grows most with rate** (blocking outbound executor — up to 50 dedicated worker threads on top
of the Tomcat baseline, Experiment A's exact subject). **P3-B is lower and grows more modestly**
(Servlet/Tomcat threads + shared write executor, no blocking outbound workers). **P3-C is flat at
37 regardless of rate** (no Servlet container, no blocking executor — purely Reactor Netty
event-loop threads, Experiment B's exact subject). This is directionally consistent with the
Phase 2 VT-Limited platform-thread reduction finding and with what Experiments A/B are designed to
measure — a useful Screening-level sanity check, not a Formal result.

`process_cpu_usage` (Micrometer, fraction of available CPU) stayed low and noisy (roughly
0.0006–0.05) at every rate in this Screening pass — too small a load, on too little wall-clock
time (25s pilots), to draw a reliable CPU trend from; Formal's longer, repeated runs are needed for
that.

### RSS — data quality issue found, not reported

The RSS sampler in `scripts/run-phase3-native-screening.sh` captured the Gateway JVM's PID via
`$!` after a `nohup`/`env` chain. Direct comparison against `ps aux` after the sweep showed this
PID **did not match the actual running java process** in every run — the sampler was tracking the
wrong (and apparently short-lived) process the whole time, producing a flat, implausibly-small
~1.5MB reading that is not real RSS data. `jvm_threads_live_threads`/`process_cpu_usage` above are
unaffected (read live from the Gateway's own `/actuator/prometheus` HTTP endpoint, independent of
PID tracking) — only RSS is discarded from this Screening pass. **Fixed in the script** (now
resolves the PID via `pgrep -f <jar basename>` after the health check, confirmed correct by a
clean process-leak check across the R7 re-run) — verified working for the next run onward, but no
Screening RSS numbers from this pass are reported, since re-running the full sweep solely to
recover RSS was not warranted at Screening rigor. Formal must confirm the fix holds before relying
on Formal RSS numbers.

## 7. Validity / environment notes

- Ran natively on macOS (arm64), Docker Desktop not involved in this pass (native JVM/uvicorn
  processes only, per `docs/decisions/phase2-formal-native-macos-environment.md`'s general native
  methodology — not re-verified as a full Formal-grade preflight here, since this is Screening).
- Fresh Mock LLM process per config's rate sweep is not required by Unit 5's per-scenario freshness
  rule (that rule is for the functional cross-validation harness); this Screening harness starts
  one fresh Mock + Gateway pair per (config × rate) run instead, which is stricter, not looser.
- One real bug found and fixed in the harness itself (RSS PID capture, §6) — not a Gateway bug.
- No stop-condition triggers observed (§`docs/test-plan/phase3-design.md` §17 list): no unexpected
  exceptions in any `gateway.log`/`k6-output.txt`, no failure bursts, no counter plateau, no
  dropped_iterations, no postflight gauge leak (`gateway_admission_active`/`gateway_active_streams`/
  `gateway_upstream_active`/write-executor/connection-pool gauges all 0 after every run), no
  connection-pool pending buildup (`reactor_netty_connection_provider_pending_connections` stayed
  0 throughout for P3-B/C at every rate, including R10 — the pool's `maxConnections=50` ceiling
  was never actually approached because admission itself rejects before the pool would see more
  than 50 concurrent acquires).

## 8. Formal load candidates (selected, not yet run as Formal)

| Candidate | Rate | Rationale |
|---|---|---|
| **Stable** | R3 | 0 rejections at every implementation; clean baseline well under the ≈6.4 req/s ceiling |
| **Near-capacity** | R7 | First rate with real, small, consistent rejection rate (~8.5%, 15/175–176) in all three — sits right at the measured capacity boundary |
| **Over-capacity** | R10 | Clear, unambiguous overload (~35% rejection, 87/250–251) in all three — sustained backlog behavior, not a borderline case |

Same three rates apply to all three implementations (Unit 6 §26/`docs/test-plan/phase3-design.md`
requirement — pairwise Experiment A/B comparisons need a shared load axis, not
per-implementation-tuned rates).

## 9. Formal matrix candidate (proposed, not started)

3 implementations (P3-A/B/C) × 3 load points (R3/R7/R10) × repeats-per-cell (count and warm-up
length TBD at Unit 6.5 Formal Protocol Freeze, following the Phase 2 precedent of 3x repeats per
cell) = the Formal grid to freeze at Unit 6.5. This document does not start that Formal run.
