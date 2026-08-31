# ADR: Phase 4 Open-model LoadGen VU Sizing (Final)

Status: Accepted (Phase 4 Open Unit 7.3 scope)

## 1. Root cause of M1 R=10's `dropped_iterations`

Audited `unit7-open-screening/m1-r10-screen1-postvufix/` (raw logs unchanged; only its derived
`result.json`/`validity.json` were re-collected under the reverted `dropped_eq_0` semantic — see
`docs/test-results/phase4/unit7.3-open-control-amendment/SUMMARY.md`). Configuration at the time:
`PRE_ALLOCATED_VUS=200`, `MAX_VUS=800` (the old `max(RATE*20,100)`/`max(RATE*80,800)` formula).

**`vus.max = 450` in that run — well under the configured `MAX_VUS=800` ceiling.** Per the governing
instruction's own branching (§10: "붙었다면 LoadGen headroom 부족, 붙지 않았다면 다른 원인 조사"), the
ceiling was *not* reached, so `dropped_iterations=251` is not explained by "the cap was too low."

The actual mechanism: M1 was independently experiencing a genuine reliability failure at R=10
(`classification=MODEL_TIMEOUT`, `failed_mid_stream=294`, `executor_queue_depth` peaking at 400 of
its configured `PT_QUEUE_CAPACITY=500`). Required concurrent VUs grew from a nominal ~80
(`10 req/s × ~8s`) to a peak of 450 as M1's own response times degraded toward its
`CHAT_TOTAL_TIMEOUT_MS=60000` ceiling. The old formula only **pre-allocated 200** VUs and relied on
k6's live, on-demand VU-pool growth to reach 450 — i.e., dynamic growth had to more than double the
pool *during* the exact window when the model was degrading fastest. `dropped_iterations` is the
signature of that growth not keeping pace with a sudden concurrency spike, not of the final ceiling
being undersized.

**Conclusion: this is a LoadGen VU-pool *growth-pacing* problem, not (only) a ceiling-sizing
problem — and not a Gateway/model finding in itself**, though it was triggered by a genuine M1
reliability event. Per the reverted `dropped_eq_0` policy, this run correctly stays INVALID as a
measurement of R=10 either way; the fix here is to stop it from recurring.

## 2. Fix: pre-allocate closer to the timeout-bound worst case, not the nominal case

Relying on live dynamic growth from a small nominal-sized base is exactly what failed. The fix is to
**pre-allocate** enough VUs up front that a sudden degradation toward the app's own worst-case bound
doesn't require growing the pool live at all, while keeping a separate, larger `MAX_VUS` as a true
emergency backstop with additional headroom:

```
CHAT_TOTAL_TIMEOUT_S = 60          # already-frozen app-level bound (CHAT_TOTAL_TIMEOUT_MS=60000)

PRE_ALLOCATED_VUS = ceil(RATE * CHAT_TOTAL_TIMEOUT_S * 1.0)
MAX_VUS           = ceil(RATE * CHAT_TOTAL_TIMEOUT_S * 1.25)
```

Both scale with the *run's own* target `RATE`, not with `SAFE_OPEN_MAX` — a run at R=10 pre-allocates
for R=10's worst case (600), not for R=256's (15360), per the explicit instruction not to blanket
over-allocate every run at the global ceiling.

| RATE | PRE_ALLOCATED_VUS | MAX_VUS |
|---|---|---|
| 10 (would have covered the observed 450 peak with margin) | 600 | 750 |
| 160 | 9600 | 12000 |
| 256 (SAFE_OPEN_MAX) | 15360 | 19200 |

A generous defensive cap (`PRE_ALLOCATED_VUS` capped at 20000, `MAX_VUS` capped at 25000) is retained
in code purely as a last-resort guard against a misconfigured `RATE` far outside the frozen range
(2–256); it is not expected to bind anywhere in that range and did not bind in validation (§3).

## 3. Host-safety validation (Section 12 — empirical, not assumed)

Ran a Direct-Mock dry-run at `RATE=256` (the top of the frozen range), `PRE_ALLOCATED_VUS=15360`,
`MAX_VUS=19200`, warmup=10s/measurement=30s (a short sanity window, not a full canonical/diagnostic
window — this run's only purpose is VU-safety validation, and it is not treated as a canonical or
diagnostic Screening artifact):

- `iterations = http_reqs = client_sse_open_success_total = 10241`, **zero gap, zero dropped, zero
  zero-event-terminals** — fully clean.
- Actual peak concurrent VUs used: `vus.max = 2146` (matches Little's Law nominal estimate,
  `256 × ~8.1s ≈ 2074`, closely) — nowhere near the 15360 pre-allocated, confirming the large
  pre-allocation is a safety margin, not something every run actually consumes.
- k6 process RSS peaked at **~3.05 GB** (`/tmp/vu_safety_k6_rss.csv`, not preserved as a permanent
  artifact — this was a throwaway validation run per this Unit's "no new canonical/diagnostic
  artifact" scope, values recorded here instead), CPU 28–39% of one core, FD count peaked ~3429.
- Host has 16 GB total RAM (`sysctl hw.memsize`). ~3.05 GB for k6 at the rare top-of-range R=256
  point, alongside Gateway/Mock/Prometheus (individually observed in the hundreds-of-MB-to-low-GB
  range in prior units), leaves several GB of headroom — not judged a host-safety risk, though this
  is a measured data point, not a formal guarantee for arbitrarily long runs.

**Validated safe for the frozen Open rate range (2–256).**

## 4. Scope

Applies to `scripts/run-phase4-open-benchmark.sh`'s `PRE_ALLOCATED_VUS`/`MAX_VUS` computation only.
`load-test-k6/scenarios/05-phase4-open-arrival-rate.js`'s workload semantics are unchanged — VU
counts are passed in as `-e` environment values the scenario already reads
(`__ENV.PRE_ALLOCATED_VUS`/`__ENV.MAX_VUS`), so no scenario-file change is needed at all.
