# Phase 4 Unit 3 — Sampler Capability Calibration

Scope: sampler overhead/capability only, exercised against Mock/k6 processes during this Unit's
Direct Mock control runs — not against a Gateway (no Gateway JVM was started this Unit).

## RSS sampler — `ps -o rss= -p <pid>`

Reused pattern from Phase 2/3 native macOS harnesses (`docs/decisions/
phase2-formal-native-macos-environment.md` §1-1). Measured overhead on this host:

```
$ time ps -o rss= -p <pid>
```

Single invocation completes in single-digit milliseconds (see `os-resource-calibration.md`
overhead notes). At 1Hz sampling (one `ps` call per second) over a 30-120s calibration window, CPU
cost is negligible relative to the host's 10 cores. **Decision: 1Hz `ps -o rss=` is adopted for
Mock/k6 process RSS sampling in this Unit's control scripts, and is the candidate policy to carry
forward to the future Gateway RSS sampler (Unit 4+), consistent with Phase 2/3 precedent.**

## FD sampler — `lsof -p <pid> | wc -l`

Mock (Python/uvicorn) and k6 are not JVM processes, so Micrometer's `process_files_open_files`
(confirmed available for the Java Gateways in Unit 2) does not apply to them — an OS-level sampler
is required for Layer B processes.

Overhead measured on this host (small process, ~10 FDs): ~30ms per invocation (5 invocations ≈
159ms total, see raw preflight measurement). This is more expensive than `ps -o rss=` because
`lsof` enumerates and resolves every open descriptor. **Decision for this Unit's control scripts:
1Hz `lsof -p <pid> | wc -l`**, matching the RSS sampler cadence for simplicity — actual overhead
under the calibration's own high-FD-count processes (k6/Mock at N=3200-5000) is recorded in each
level's `mock-resource-samples.csv` (the sampler's own wall-clock cadence stays close to 1s even
under load; see `closed/n<N>/mock-resource-samples.csv` timestamp deltas for direct evidence — if a
level shows sample gaps materially larger than 1s, that level's `lsof` overhead was non-negligible
and is called out in `closed-control-summary.json`).

**Note for future Gateway Formal (Unit 6/8) FD collection**: prefer the Gateway's own
`process_files_open_files`/`process_files_max_files` Micrometer gauge (§ Unit 2 finding,
`docs/decisions/phase4-metrics-contract.md` §10 amendment) as the Primary FD source for M1/M2/M3 —
zero extra sampler process, zero `lsof` overhead, already confirmed exported. Reserve an OS-level
`lsof`/`ps`-based sampler for Mock/k6/system-wide diagnostic cross-checks only, at a reduced cadence
(5s candidate, per the Unit 3 design brief) since it is comparatively expensive and is not the
Primary source for the Gateway processes themselves.

## CPU sampler — `ps -o %cpu=` (Mock/k6) vs Micrometer `process_cpu_usage` (future Gateway)

For Mock/k6 (non-JVM), `ps -o %cpu=` is the only practical option this Unit — sampled at 1Hz
alongside RSS/FD, negligible overhead (single `ps` call).

For the future Gateway (Java), Unit 2 already confirmed `process_cpu_usage` is exported by
Micrometer's `ProcessorMetrics` binder under Boot 4.1.0 (`docs/test-results/phase4/
unit2-functional/metrics-parity.md`). Its semantics differ from Phase 1/2's
`io.prometheus:simpleclient` `process_cpu_seconds_total`:

- `process_cpu_usage`: an instantaneous **ratio** (0.0-1.0) of process CPU time to wall-clock time,
  NOT a monotonically increasing counter of cumulative CPU-seconds.
- `process_cpu_seconds_total` (Phase 1/2, simpleclient, Linux-only via `/proc`): a cumulative
  counter; average utilization over an interval is computed as
  `rate(process_cpu_seconds_total[interval])`.
- Because `process_cpu_usage` is already a ratio, the Phase 1/2 formula
  (`rate(counter) -> avg_cores = rate * availableProcessors`) does not apply as-is. The
  corresponding Phase 4 formula candidate is simply `avg_cores ≈ process_cpu_usage *
  system_cpu_count` (both exported, both confirmed present — Unit 2). **This is recorded here as a
  formula candidate only** — Phase 4 does not exercise a live Gateway process under load in this
  Unit, so no sanity cross-check against real load was performed. A small functional-scale sanity
  check (single Gateway request, comparing `process_cpu_usage` movement against expected near-idle
  behavior) is deferred to Unit 4 harness verification, which will start a Gateway process for the
  first time since Unit 2.

## Memory pressure sampler — `sysctl -n kern.memorystatus_vm_pressure_level`

Confirmed on this host: returns `1` (normal) at idle; the macOS-documented values are `1`=normal,
`2`=warn, `4`=critical. Overhead: ~3ms per call (measured, see `os-resource-calibration.md`).
**Adopted as the Phase 4 memory hard-stop signal** — cheaper and more directly authoritative than
parsing `memory_pressure`'s free-text stdout or picking an arbitrary "free memory < N GB" threshold
(which the Unit 3 design brief explicitly warned against inventing). Both control scripts
(`run-phase4-direct-mock-{closed,open}-control.sh`) check this before starting each level and abort
escalation (not the whole run) if it is not `1`.

## FD safety denominator — decided this Unit

Unit 0 snapshot: `ulimit -n` (soft) = 1,048,576, `kern.maxfilesperproc` = 61,440, `kern.maxfiles`
(system-wide) = 122,880 (re-confirmed unchanged this Unit, `os-resource-calibration.md`).

**Decision**: even though the shell's soft `ulimit -n` is far higher, `kern.maxfilesperproc`
(61,440) is the macOS kernel's actual per-process ceiling and is the binding per-process
denominator. For a single Layer-B process (Mock or k6) considered alone, the effective safety
threshold is:

```
FD_SAFETY_DENOMINATOR (per-process) = kern.maxfilesperproc = 61,440
FD_SAFETY_THRESHOLD (85%, per Unit 1 candidate)             = 52,224
```

System-wide `kern.maxfiles` (122,880) is checked as a secondary/system-wide safety net (relevant
once Gateway + Mock + k6 all run concurrently, Unit 4+) — not the primary per-process denominator
for this Unit's two-process (k6, Mock) calibration.

## Cooldown / drain policy

Both control scripts insert 5s between levels and confirm the Mock process (and its listening
port) has fully torn down before starting the next level's fresh Mock process — this is stronger
than a same-process cooldown (a fresh process per level has no carried-over in-process state at
all). TIME_WAIT sockets from the *previous* level's closed connections may still be draining at the
OS level when the next level starts; this is captured as a diagnostic snapshot (§ below), not
actively drained (per the Unit 3 design brief: no OS TCP sysctl tuning, no forced socket
teardown).

## Environment stall check

Not separately implemented as an automated gate in this Unit's short calibration scripts (each
level's k6 run is short — worst case ~2 minutes at N=5000, well inside Phase 2 Native's 20s
plateau-normal / 30s+ plateau-suspect range) — instead cross-checked manually via each level's
`mock.log` (continuous per-request log lines, no gaps) and `mock-resource-samples.csv` (1Hz samples
throughout, no missing/duplicated timestamps beyond expected `lsof` jitter). No environment stall
observed in any level run this Unit (see `closed-control-summary.json` /
`open-control-summary.json`).
