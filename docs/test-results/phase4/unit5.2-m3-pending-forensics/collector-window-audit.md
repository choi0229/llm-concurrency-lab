# Collector measurement-window audit — M3 pending validity check

## What window does the Prometheus range query actually use?

`scripts/run-phase4-closed-benchmark.sh` (frozen, unmodified — confirmed by SHA256/mtime in the
Unit 5 preflight):

```bash
# line 165-177
# Prometheus range query covering the whole run window, generous margin.
Q_START=$(( (PROCESS_START_MS/1000) - 2 ))
Q_END=$(( (DRAIN_END_MS/1000) + 2 ))
for metric in ... reactor_netty_connection_provider_pending_connections; do
  curl ... --data-urlencode "start=${Q_START}" --data-urlencode "end=${Q_END}" ...
done
```

`PROCESS_START_MS` is captured before Gateway/Mock/Prometheus are even started (line 70, at the
very top of the harness). `DRAIN_END_MS` is captured after the post-wave drain loop finishes.

**Verdict on forensics brief §2's A/B/C/D question: this is Case D — process lifetime, full
stop.** The query window is *not* scoped to the canonical wave; it covers Gateway/Mock/Prometheus
startup, the 5-request sequential prewarm (`setup()`), the canonical wave, and drain, all in one
range. This is true for every metric in that loop, not just the pending metric — `range_query_peak()`
in `collect_phase4_closed_result.py` takes the max over whatever series comes back for that whole
window, with no window-narrowing step for any of them.

## Does this match the frozen design intent for the pending anomaly check specifically?

Two separate, pre-existing design statements were found (neither is the collector's own
docstring — both predate Unit 5):

1. `docs/decisions/phase4-metrics-contract.md` §14 (postflight/steady-state cleanliness list):

   > M3만: `<reactor_netty_connection_provider_pending_connections 또는 동등 candidate> == 0`

   This reads as a **postflight** ("is the pool back to idle after the run") check, analogous to
   `gateway_active_requests_final==0`. It is not what `m3_pending_no_anomaly` implements —
   `postflight_clean` already covers final-state gateway/mock activity separately, and
   `m3_pending_no_anomaly` is driven by the whole-window `peak`, not a final-snapshot value.

2. `docs/test-plan/phase4-design.md` §7-1 (hidden-limiter detection, the section this check is
   actually meant to implement):

   > Primary Normal workload 실행 중 `pending acquire`(§metrics-contract.md 후보 metric)가
   > **지속적으로 0보다 크면** hidden limiter/anomaly로 취급한다.

   ("If `pending acquire` is **continuously/sustained** greater than 0 during Primary Normal
   workload execution, treat it as a hidden limiter/anomaly.")

## Actual implementation (`collect_phase4_closed_result.py` line 282-288)

```python
pending = range_query_peak(run_dir / "prom-reactor_netty_connection_provider_pending_connections.json")
m3_pending_anomaly = bool(pending and pending.get("peak", 0) and pending["peak"] > 0 and (target_concurrency or 0) < 800)
```

This flags on **any single non-zero sample anywhere in the peak** (`peak > 0`), over the **whole
process-lifetime window** (Case D above) — not "sustained," and not scoped to "Primary Normal
workload 실행 중" (the canonical wave only).

## Gap identified

The §7-1 design text specifies a **sustained/continuous** condition during the **wave only** as
the anomaly signal. The shipped collector implements a **single-sample, whole-process-lifetime
peak > 0** check. These are measurably different thresholds:

- A single 1-second spike during pool ramp-up (as observed here) satisfies the implemented check
  but would *not* satisfy a literal reading of "지속적으로" (sustained/continuous).
- The implemented window (process lifetime) is broader than "Primary Normal workload 실행 중"
  (wave only) — in this specific run the flagged sample happened to fall inside the wave anyway
  (see `timeline.md`), so the broader window did not by itself cause a false attribution here, but
  it is a real latent gap: a prewarm- or drain-only transient would currently be flagged
  identically to a wave-time one, which the design text does not call for.

This is reported as a design-vs-implementation gap for the user's judgment. **No change has been
made to `collect_phase4_closed_result.py` or to any frozen file.** Per the forensics brief §15,
this finding is not itself license to relax the threshold — it is evidence for an amendment
proposal, subject to the STOP → proposal → approval → regression → re-collection-scope sequence.
