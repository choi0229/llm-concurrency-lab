# Phase 4 Unit 3 — Control Capacity / OS / Load Generator Calibration Summary

**Scope reminder**: this Unit measures whether Mock LLM + k6 + this macOS host can support Phase 4's
planned scalability test range. **No Gateway process was started this Unit.** No M1/M2/M3
comparison, no Gateway concurrency/RPS boundary, no MSC/MRC/MSAR judgment was made.

## 1. Environment preflight

- Native arm64, no Rosetta, Docker Desktop off, no stale Gateway/Mock/k6 processes, target ports
  clean — all confirmed (`environment.json`).
- **Advisory, not blocking**: running on Battery Power (90%, 13h26m remaining) rather than AC;
  Chrome Helper (Renderer) observed at ~17.4% CPU during preflight. Neither was judged severe
  enough to block starting — both are flagged as plausible contributors to two anomalies found
  during calibration (see §3, §4).

## 2. OS capability re-confirmation (read-only, unchanged from Unit 0)

`ulimit -n`=1,048,576 (soft), `kern.maxfilesperproc`=61,440, `kern.maxfiles`=122,880, ephemeral
ports 49152-65535, 10 logical CPUs, 16GB RAM, no swap in use, memory pressure normal (level=1).

**FD safety denominator decided**: `kern.maxfilesperproc` (61,440), not the much larger soft
`ulimit -n` — this is macOS's actual per-process kernel ceiling. 85% threshold = 52,224.

**Memory safety signal decided**: `sysctl -n kern.memorystatus_vm_pressure_level` (1=normal,
2=warn, 4=critical) — cheap (~3ms/call), authoritative, adopted over parsing `memory_pressure`
text output or inventing an arbitrary free-memory threshold.

**Ephemeral port / two-leg caveat**: Direct Mock control here exercises only ONE connection leg
(k6→Mock). A future Gateway test adds a second leg (Gateway→Mock) — FD/port pressure per logical
stream roughly doubles. This is **not proven safe by this Unit's results alone** and is carried
forward explicitly as an open risk for Unit 4+, not resolved here.

## 3. Direct Mock Closed-model control calibration

Levels tested: 100, 200, 400, 800, 1600, 3200 (stopped before 5000 — first real failure hit at
3200, no auto-doubling past it, per design).

| N | completed | failed | ttfc p95 | duration p95 | classification |
|---|---|---|---|---|---|
| 100 | 100 | 0 | 1.03s | 7.91s | CLEAN |
| 200 | 200 | 0 | 1.05s | 7.94s | CLEAN |
| 400 | 400 | 0 | 1.10s | 7.99s | CLEAN |
| 800 | 800 | 0 | 1.18s | 8.08s | **CLEAN, robust** |
| 1600 | 1600 | 0 | 2.09s | 9.15s | **CLEAN but marginal** — see below |
| 3200 | 2415 | 785 | 6.93s | 13.93s | **FAIL** |

**N=1600 marginal-classification rationale**: this level was run twice (once during initial script
debugging, once in the final clean rerun). The two runs gave TTFC p95 of 5.03s and 2.09s
respectively — a ~2.4× run-to-run variance at the *same* nominal load, most plausibly attributable
to host-level noise (Chrome CPU contention observed at preflight) rather than a hard, reproducible
environment ceiling. Both runs technically passed the strict `completed==N && failed==0` gate, but
the instructions explicitly require "TTFC/duration p95가 baseline에서 비정상적으로 폭증하지
않음" as a PASS condition — a 2-5× TTFC blowup with high run-to-run variance does not meet that bar
confidently. **N=800, by contrast, showed low variance and only mild (~20%) TTFC/duration
increase across runs — this is the level used as the basis for headroom, not the higher but
unstable N=1600.**

**N=3200 best-supported classification (not a confirmed kernel-level root cause)**: k6's own peak
CPU (65.4%) and FD count (3214, ≈N) climbed faster and higher relative to load than Mock's (peak
CPU 48.4%, FD 2476, well below N) — Mock's own metrics show zero
`mockllm_failed_requests_total`/`mockllm_cancelled_requests_total`, and
`mockllm_completed_requests_total` exactly matches k6's completed count, meaning the 785 "failed"
streams never reached Mock's application handler at all. This is the core evidence for classifying
**LOADGEN_LIMIT as the best-supported/primary classification** (k6 and/or OS-level
connection-establishment congestion under ~3200 near-simultaneous connection attempts), with a
possible **DOWNSTREAM_LIMIT** contribution from Mock's single-process asyncio/uvicorn accept path
not ruled out. No packet-level tracing was performed (out of Unit 3 scope), so this is reported as
the best-supported classification given the available evidence, not a confirmed root cause.

**Derived**: `SAFE_CLOSED_MAX = 800 / 1.25 = 640`.

**M1 architecture-boundary headroom check**: M1's hard ceiling is 550 (50 workers + 500 queue).
`800 / 550 ≈ 1.45×` ≥ the 1.25× minimum — **no CONTROL BLOCKER**. M1's own architecture boundary
can be observed in future screening without the control environment itself becoming the limiter.

Full evidence: `closed/n<N>/{k6-summary.json,k6-stdout.log,mock.log,mock-metrics-{before,after}.txt,
mock-resource-samples.csv,k6-resource-samples.csv,validity.json}`, `closed-control-summary.json`.

## 4. Direct Mock Open-model control calibration

Rates tested: 10, 20, 40, 80, 160, 320 req/s — **all six completed clean**, 0 rejected/failed/
dropped at every rate, actual started rate matched target almost exactly (e.g. 320.00/s measured
against 320/s target).

| R (req/s) | started | actual rate | completed | rejected/failed/dropped | classification |
|---|---|---|---|---|---|
| 10 | 301 | 10.03 | 301 | 0/0/0 | CLEAN |
| 20 | 601 | 20.03 | 601 | 0/0/0 | CLEAN (measurement artifact, see below) |
| 40 | 1201 | 40.03 | 1201 | 0/0/0 | CLEAN |
| 80 | 2401 | 80.03 | 2401 | 0/0/0 | CLEAN |
| 160 | 4800 | 160.00 | 4800 | 0/0/0 | CLEAN (k6 CPU peak 97%) |
| 320 | 9600 | 320.00 | 9600 | 0/0/0 | CLEAN |

**Important measurement-tooling finding (R=20)**: the custom JS-computed
`client_stream_duration_seconds` Trend metric reported p95=104.9s / max=104.97s — wildly outside
the normal ~7.8-9s range. Investigation showed k6's own **built-in** `iteration_duration` and
`http_req_duration` metrics (computed by k6's Go runtime, not JS) stayed completely normal for all
901 iterations that run (avg=7.85s, max=8.09s). This means the actual network requests were fine —
the anomaly was in the *custom metric's own JS-side `Date.now()` timing*, most likely a
transient JS-VM scheduling delay under host contention (consistent with the Chrome CPU load noted
at preflight), not a real service degradation. **Classified CLEAN based on the authoritative
built-in metrics.** Recorded as a methodology finding for Unit 7+: cross-check custom Trend metrics
against k6's built-in `iteration_duration`/`http_req_duration` during Open screening/Formal rather
than trusting the custom metrics in isolation.

**R=160 note**: k6's own process hit 97% CPU (near single-core saturation) — a genuine LOADGEN
capacity warning for rates meaningfully beyond the tested range, though this run itself completed
cleanly.

**Derived**: `SAFE_OPEN_MAX = 320 / 1.25 = 256` req/s. Per design brief instruction, 320 was NOT
auto-extended to 640/1280 even though clean — this Unit confirms control capability only up to the
originally-frozen candidate ceiling.

M1 sanity-only reference (Little's Law, NOT a capacity determination): 50 workers / ~7.8s service
time ≈ 6.4 req/s raw steady-state ceiling — far below `SAFE_OPEN_MAX`, so M1's own open-model
boundary (wherever it actually falls, to be measured in Unit 7) is nowhere near control-limited.

Full evidence: `open/r<R>/{...}`, `open-control-summary.json`.

## 5. LoadGen / Mock resource headroom

Both processes stayed well short of hard resource exhaustion across the full tested range:

- k6 peak CPU: 19.9% (closed N=800) → 65.4% (closed N=3200, contributing to that level's failure);
  open-model peak 97% at R=160 (still completed clean).
- Mock peak CPU: 34.8%-70.6% across all levels, generally lower than k6's climb rate — Mock was
  **not** the first process to show saturation-level resource pressure in either model.
- FD counts tracked roughly 1:1 with N/R for both processes at clean levels, confirming FD was not
  a hidden limiter within the tested range (`kern.maxfilesperproc`=61,440 was never approached —
  peak FD observed was 4,595 at open R=320, ~7.5% of the safety denominator).

**No LOADGEN_LIMIT or DOWNSTREAM_LIMIT was observed within the frozen SAFE_CLOSED_MAX(640)/
SAFE_OPEN_MAX(256) ranges** — both boundaries sit comfortably below where degradation/failure was
actually observed (closed: degradation from ~N=1600, failure at N=3200; open: no failure observed
up to 320, LOADGEN CPU warning at 160).

## 6. Sampler capability — policy decided

- **RSS**: `ps -o rss=`, 1Hz — negligible overhead, adopted for Mock/k6 this Unit and as the
  candidate for the future Gateway RSS sampler.
- **FD (non-Java)**: `lsof -p <pid> | wc -l`, 1Hz — ~30ms/call overhead on a small process; used
  for Mock/k6 this Unit.
- **FD (future Gateway)**: prefer Micrometer's own `process_files_open_files` (confirmed exported,
  Unit 2) — zero extra sampler process needed. `lsof` reserved for Mock/k6/system diagnostic only.
- **CPU (non-Java)**: `ps -o %cpu=`, 1Hz.
- **CPU (future Gateway)**: `process_cpu_usage * system_cpu_count` formula candidate (both
  confirmed exported, Unit 2) — **not yet sanity-checked against a live Gateway under load**
  (no Gateway ran this Unit); deferred to Unit 4.
- **Memory safety**: `kern.memorystatus_vm_pressure_level` — see §2.

Full rationale: `sampler-capability.md`.

## 7. Cooldown / drain / environment stall

5s cooldown + fresh Mock process per level was sufficient — post-calibration TCP snapshot shows
only 1 TIME_WAIT / 14 ESTABLISHED (baseline-normal), no accumulation across the ~15 total
closed+open levels run this Unit. No environment stall (log gaps, counter plateaus) observed in any
`mock.log`/resource-sample file this Unit.

## 8. M3 `WEBCLIENT_MAX_CONNECTIONS` — frozen this Unit

```
SAFE_CLOSED_MAX = 640
WEBCLIENT_MAX_CONNECTIONS (screening) = 640 × 1.25 = 800
WEBCLIENT_PENDING_ACQUIRE_MAX_COUNT (screening) = 800
```

Recorded in `docs/test-plan/phase4-design.md` §7-1-1. M3's code default (100) is left unchanged
(`FUNCTIONAL_ONLY_NOT_PHASE4_CAPACITY_CONFIG`) — Unit 4/5 harnesses will override via env.

## 9. Unit 0-2 wording/contract cleanup (§0 of this Unit's brief)

- `phase4-design.md` §8-1 added: precisely scopes what "parity" means (semantic, not
  client-visible-HTTP-contract) — no code changed.
- M1/M2 write-path config (`SERVLET_WRITE_POOL_SIZE=64`/`SERVLET_WRITE_QUEUE_CAPACITY=20000`/
  `SERVLET_PER_STREAM_BUFFER_CAPACITY=8`) was **not** touched this Unit — no Gateway ran, so there
  was no evidence to justify (or require) an amendment either way.

## 10. Overall PASS/FAIL

**PASS.** SAFE_CLOSED_MAX=640, SAFE_OPEN_MAX=256 established with real evidence, no CONTROL
BLOCKER found (M1's 550-task hard boundary has 1.45× headroom within SAFE_CLOSED_MAX). Two
methodology findings (N=1600 closed-control instability, R=20 custom-metric measurement artifact)
were investigated, root-caused as far as Unit 3's scope allows, and did not require lowering the
frozen safe ranges below what robust evidence supports.
