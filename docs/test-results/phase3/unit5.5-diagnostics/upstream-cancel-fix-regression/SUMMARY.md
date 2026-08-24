# gateway_upstream_cancel_total Semantic Cleanup — Regression Evidence

Status: Unit 5.5 §0, before the D1/D2 diagnostics
Date: 2026-08-22

## Canonical semantic (frozen)

`gateway_upstream_cancel_total` := "Gateway asked an **already-started** upstream call/
connection/subscription to cancel." A rejected request (admission failed) never reaches
`openStream()` — there is no upstream call to cancel — so `rejected` must not increment this
counter.

## What was wrong

P3-A/B's `RequestLifecycle.tryTerminate()` incremented it for **any** non-`completed` outcome,
including `rejected`. P3-C's reject path never constructs a `RequestLifecycle` at all (returns
directly from the handler before admission-success bookkeeping), so it already matched the
canonical semantic by construction — no P3-C change needed.

## Fix

One-line condition change in `gateway-mvc-blocking-spring5` and `gateway-mvc-webclient`'s
`RequestLifecycle.tryTerminate()`:

```diff
- if (!"completed".equals(outcomeValue)) {
+ if (!"completed".equals(outcomeValue) && !"rejected".equals(outcomeValue)) {
      metrics.recordUpstreamCancel();
  }
```

No change to admission, lifecycle control flow, timeout, write path, executors, WebClient, or
P3-C. Both modules' full unit test suites re-run (29/32 tests respectively) — all pass unchanged
(no existing test asserted the old, incorrect behavior).

## F3 regression (this directory: `A/`, `B/`)

`CHAT_ADMISSION_LIMIT=1`, request 1 long-running + request 2 immediate reject, same scenario as
Unit 5's F3.

| | before fix (Unit 5) | after fix |
|---|---|---|
| P3-A `rejected`Δ | 1 | 1 |
| P3-A `upstream_cancel`Δ | 1 | **0** |
| P3-B `rejected`Δ | 1 | 1 |
| P3-B `upstream_cancel`Δ | 1 | **0** |
| P3-C `rejected`Δ | 1 | 1 (unchanged) |
| P3-C `upstream_cancel`Δ | 0 | 0 (unchanged) |

All three now identical: `rejected`Δ=1, `upstream_cancel`Δ=0, `gateway_upstream_active`=0
postflight in all three. See `A/before.txt`/`A/after.txt`, `B/before.txt`/`B/after.txt` for the raw
Prometheus snapshots.
