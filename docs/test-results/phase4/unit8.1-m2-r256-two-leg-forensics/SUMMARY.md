# Phase 4 Open Unit 8.1 — M2 R=256 Two-Leg Transport / Ephemeral-Port Forensics

**Status: COMPLETE. Verdict = Case B — SHARED-HOST TRANSPORT RESOURCE CONFOUND.**

Diagnostic only. Neither live run is a canonical Screening/Formal point; not to be used in any MSAR
bracket, GREEN/AMBER/RED count, or Open Formal replicate. `unit8-open-screening/m2-r256-screen1/`
raw untouched (read-only).

## Artifacts

| file | content |
|---|---|
| `jdk-httpurlconnection-audit.md` | Temurin 21.0.11+10 `sun.net.www` source: `disconnect()` semantics (static phase). |
| `m1-m2-outbound-parity.md` | M1/M2 `common/` byte-identical (static phase). |
| `bindexception-audit.md` | The canonical run's 9 BindExceptions dissected (static phase). |
| `population-accounting.md` | §15 table from canonical raw (static phase). |
| `direct-mock-r256-result.md` | **Live run 1** — k6→Mock direct R=256. §10 gate = **PASS**. |
| `m2-r256-diagnostic-result.md` | **Live run 2** — M2 two-leg R=256. Case B verdict, full evidence. |
| `population-accounting-final.md` | §17 table from both live runs; both deficits closed. |
| `direct-mock-r256/`, `m2-r256-two-leg/` | raw run dirs: `two-leg-socket-timeline.csv`, `raw-socket-observations.csv` (95 MB / 139 MB — full raw rows preserved per §10/§29, epoch/leg/local/foreign/state), `lsof-ownership-samples.txt`, `k6-summary.json`, `prom-*.json`, `gateway.log`, `result.json`. |

## What the two live runs established

### Run 1 — Direct Mock R=256 (`direct-mock-r256/`), §10 gate = PASS

k6 → Mock `:8000` direct, one run. `open_attempt == open_success == first_event == 46080` (every
iteration), `zero_event_terminal = 0`, `dropped = 0`, arrival 30716/30720, Mock completed
46080/46080 (`cancelled 0`, `failed 0`), **`SYN_SENT` peak 1**, **zero `EADDRNOTAVAIL`**. Single
k6→Mock leg peaked at **10324 / 16384 ephemeral ports (63%)** with ~6060 free.
→ **the single-leg k6→Mock R=256 path is transport-clean. Case A (loadgen/client/host control
limit) is ruled out.** (Caveat: a k6-side latency tail — completed-duration p95 45 s vs k6's own
`iteration_duration` p95 9 s — from k6 goroutine-scheduler starvation at `vus.max ≈ 2495` on a
fully-successful load; not a transport failure, and absent in Run 2 where k6 was less loaded.)

### Run 2 — M2 R=256 two-leg (`m2-r256-two-leg/`)

Reproduced the canonical failure: `zero_event_terminal = 6279` / 30719 (canonical: 5688 / 30721),
**16** Gateway `BindException: Can't assign requested address` (canonical: 9), `sse_error = 0`.

**Two-leg endpoint-aware socket telemetry (1 Hz):**

| | LEG A (k6→:18102) | LEG B (Gateway→:8000) |
|---|---|---|
| unique local source ports (peak) | **8282** | **8120** |
| TIME_WAIT (peak) | 7824 | 7616 |
| ESTABLISHED (peak) | 2342 | 2342 |
| SYN_SENT (peak) | 1 | 0 |

- **A ∪ B unique local ports (peak) = 16362 / 16384-port pool = 99.87%.**
- **A ∩ B (peak) = 1** — macOS does not reuse a source port across the two destinations; the legs'
  port demands are disjoint and **add**.
- `lsof`: k6 owns LEG A sockets, Gateway JVM owns LEG B sockets — two processes, disjoint ports.
- **All 16 `BindException` timestamps** fall in a second where `A ∪ B` is at/near the 16384 ceiling.
  When the pool pins, ESTABLISHED collapses on **both** legs while TIME_WAIT stays ~7000+ — classic
  "every free port in TIME_WAIT → `connect()` = `EADDRNOTAVAIL`", surfacing silently on LEG A (6279
  k6 zero-event) and as the 16 `BindException`s on LEG B. Sawtooth as TIME_WAITs age out (2·MSL=30s).

**Gateway internals all non-binding at the failure:** `virtual_tasks_active` peak 2341, mean 1643,
**declining** (first-half 1723 → second-half 1563); `process_cpu_usage` peak 0.137 (≈1.4 of 10
cores); `jvm_threads_live` 123–130 flat; `tomcat_connections_current` peak 2343/3200;
`process_files_open` 4698/1048576. **Case D (VT scheduler/execution boundary) decisively excluded.**

**Population accounting — both deficits closed, one root cause:**
`client_sse_open_success (37865) == virtual_tasks_started (37865)` — every LEG-A success reached the
Gateway; `virtual_tasks_started (37865) − Mock completed (37849) = 16` = the BindException count
exactly. LEG-A deficit 6279 + LEG-B deficit 16, both `EADDRNOTAVAIL` from the same exhausted pool.

**Clock integrity clean (Run 2):** custom JS-timed and k6 built-in network-timed spans agree
(`client_ttfc` p95 1.19 s, `iteration_duration` p95 8.99 s) — the 6279 failures are real connect
failures, not timing artifacts.

**Sampler perturbation (§5):** two-leg sampler self-measured 6.66% of one core (Run 2) / 2.53% (Run
1); aggregate < 0.5 core on a 10-core host. The effect is a fixed structural ceiling (pool = 16384;
disjoint two-leg demand sums past it), not timing-sensitive. No perturbation that changes the verdict.

## Verdict — Case B

The LoadGen (k6) and the SUT (Gateway) run on **one host** and draw from the **same 16384-entry
ephemeral-port pool**. At R=256 the k6→Gateway leg (~8282 ports) and the Gateway→Mock leg (~8120
ports) have **disjoint** port demands that sum to ~16362 ≈ the whole pool; new `connect()` on either
leg then fails with `EADDRNOTAVAIL`. This is a **benchmark-control confound**, not an M2 architecture
property and not a Virtual-Thread finding. `m2-r256-screen1` must **not** be promoted to architecture
RED (§20).

The Gateway→Mock leg's own `disconnect()`-per-request churn (source-confirmed in
`jdk-httpurlconnection-audit.md`; ~1 active close/request → ~7600 TIME_WAIT) is a genuine
contributor — but LEG B alone (~8120) fits the pool and LEG A alone (~10324, Run 1) fits the pool;
only the **sum on one host** breaks. That is Case B, not Case C.

## Canonical status (unchanged by Unit 8.1)

- **M1:** R2/R4 GREEN; R10 RED (`MODEL_REJECTION`, reproduced Unit 8).
- **M2:** R2–R160 GREEN. **R256 = PENDING FORENSIC** → now explained as a **shared-host transport
  confound at the benchmark-control level**, not a model result. Recommendation in the final report.
- **M3:** R2–R160 real canonical Screening points (**M3 R160 was executed in Unit 8** —
  `unit8-open-screening/m3-r160-screen1`, valid GREEN, ratio 1.0). **M3 R256 NOT run.**
