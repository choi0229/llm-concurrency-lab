# M2 R=160 (screen1) — Backlog / Throughput-Deficit Arithmetic

## The headline match

- Target arrival rate: 160 req/s. Measured actual arrival rate (client-side,
  `actual_started_rate`): 159.99 req/s — essentially exact.
- Measured completion throughput (measurement window, 120s): `completion_throughput = 125.6 req/s`
  (`completed=15072` over the 120s measurement window: 15072/120 = 125.6 exactly).
- Deficit: 160 − 125.6 = **34.4 req/s**.
- 34.4 req/s × 120s = **4128**, against an observed `failed_no_event = 4127`. **Match within 1
  request** (rounding).

**Correction (per user review): this match is not root-cause evidence and must not be read as
independent support for a backlog-driven explanation.** `actual_started (19199) − completed (15072)
= failed_no_event (4127)` is a direct consequence of the cohort accounting identity `actual_started ≈
completed + failed_no_event` (true here since `rejected=0` and `failed_mid_stream=0` — every
non-completed request falls into the single `failed_no_event` bucket by construction). Dividing that
identity through by the window length is *why* `(arrival_rate − completion_rate) × window` reproduces
the same number — it is the same statement rearranged, not a second, independent measurement that
happens to agree. It confirms the cohort counters are internally self-consistent
(`completion_ratio=0.785` adds up correctly); it says nothing about *why* those 4127 requests didn't
complete, and is retained below only as a consistency observation, not as evidence for any particular
causal mechanism ("backlog," "TCP refusal," or otherwise).

## Why "genuine sustained backlog growth" is not fully supported by the backlog trend itself

Per `docs/decisions/phase4-scalability-definition.md` §6 / the Open Screening protocol §2.3, backlog
is tracked as `virtual_tasks_active` / `gateway_active_requests` (both agree exactly in this run).
The recorded trend for the **measurement window**:

- First-half mean: 992.16 (virtual_tasks_active) / 992.27 (gateway_active_requests)
- Second-half mean: 862.88 / 862.95
- `rising = false`

If arrival (160/s) genuinely and continuously exceeded true sustained completion capacity throughout
the entire window, backlog should be **monotonically increasing**, not falling ~13% from the first
half to the second half. A falling-then-roughly-stable backlog is more consistent with a **transient
that is still draining from before/during the start of measurement** (e.g., pipeline fill-up
continuing past the 60s warmup boundary) than with an ongoing, worsening capacity shortfall.

## Correction: the "warmup 60s too short to fill R=160's backlog" hypothesis is withdrawn

The previous version of this document argued that `160 × 8s ≈ 1280` represented a concurrency level
that warmup needed time to "fill," and that 60s might not be enough. **This was a misapplication of
Little's Law and is withdrawn.** `L = λW` gives a steady-state *concurrency count* (1280), not a
*time value* — 1280 is not a duration to wait out. The characteristic time for the system to converge
from empty to steady-state concurrency under a step change in arrival rate is on the order of the
service time itself, `W ≈ 8s` (a new-style M/M/∞-like fill transient settles within a small multiple
of the mean service time, not within a duration proportional to the steady-state population count).
Warmup=60s is **~7.5× the nominal per-request service time**, which by simple fill-time reasoning is
generally sufficient, not marginal.

The falling first-half→second-half backlog trend (992→863) noted above therefore should **not** be
attributed to "warmup was too short" without separate, concrete evidence that the time series had not
stabilized by the end of warmup (e.g., a continuing decline visible deep into the measurement window,
or a comparison against a longer-warmup rerun). No such evidence exists in the current artifact. The
falling trend is noted as an observation that argues against a clean "continuously worsening,
still-pinned-at-cutoff" reading of Case B — it is not, by itself, attributed to any specific cause
here.

## Relationship to the population gap in `population-accounting.md`

This 4127-count backlog/deficit arithmetic (a **measurement-window-scoped, client-observed**
phenomenon) is a separate question from the 5437-count gap between k6's total iteration count (28800)
and Gateway's total dispatched count (23363) documented in `population-accounting.md` (a
**whole-process-scoped, client-vs-server accounting** phenomenon). They are of similar order of
magnitude and likely overlapping/related, but this document does not claim they are the same
mechanism — the population-accounting gap is proven to originate outside the Gateway process
entirely (Tomcat/app/Mock counters all agree exactly at 23363), whereas the backlog/throughput-deficit
arithmetic above is computed purely from client-side cohort numbers and says nothing about *where* in
the pipeline the shortfall occurs.
