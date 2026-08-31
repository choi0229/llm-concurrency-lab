# Phase 4 Unit 5.2 — M3 N=400 Pending-Acquire Anomaly Forensics

No new load was generated. This is a pure offline, read-only analysis of the existing
`docs/test-results/phase4/unit5-closed-screening/m3-n400-screen1/` artifact (unmodified,
preserved as-is) plus bytecode inspection of the exact `reactor-netty-core-1.3.6.jar` this build
resolves. No frozen file (harness/collector/config/source) was changed.

## 1-2. Metric identity and official semantic

`reactor_netty_connection_provider_pending_connections`, backed by
`ConnectionProviderMeters.PENDING_CONNECTIONS`, delegates to
`InstrumentedPool.PoolMetrics.pendingAcquireSize()` (verified via `javap` against the resolved
jar, not an external doc). Meaning: **count of `acquire()` calls currently waiting because no
idle connection is available** — acquire-wait only. See `reactor-metric-semantics.md`. The
earlier report's "idle-connection replacement/health-check" language was speculation and is
retracted.

## 3. Sample count / 4. exact timestamps / 5. labelsets

45 total samples (query window 1787921312–1787921356). Exactly **1** nonzero sample:
`t=1787921346, value=1`. Single labelset throughout
(`id=1998190683, name=phase4-webflux-pool, remote_address=127.0.0.1:8000`), identical to the
`active_connections` series for the same pool — same pool, no cross-series mixing. Full dump:
`pending-samples.csv`.

## 6. Relative-to-wave timestamps / correlation table

Wave actually started at epoch ≈1787921345.91 (k6's own `client_wave_start_epoch_ms`, spread
23ms). The pending sample (`t=1787921346`) is **+0.09s relative to wave start** — i.e. inside the
canonical wave, not prewarm, not drain. Full second-by-second Reactor/Gateway/JVM correlation:
`timeline.md` / `timeline-raw.csv`.

## 7-8. Collector aggregation logic / measurement window

`collect_phase4_closed_result.py`'s `m3_pending_anomaly = peak > 0 and target_concurrency < 800`,
computed over a Prometheus range query spanning `process_start-2s` to `drain_end+2s` — i.e. the
**entire process lifetime** (startup + prewarm + wave + drain), not a wave-scoped window. Full
audit, including a comparison against the two pre-existing design statements
(`phase4-metrics-contract.md` §14 postflight check, `phase4-design.md` §7-1 "지속적으로 0보다
크면" hidden-limiter check) and the gap between them and the implementation: `collector-window-audit.md`.

## 9. Prewarm inclusion / 10. drain inclusion

The collector's query window includes both, structurally (see above). For **this specific run**,
the one nonzero sample did **not** occur during prewarm or drain — it occurred 0.09s after actual
wave start (see §6). Prewarm held the pool at active=1, pending=0 for its entire ~39.5s duration;
drain lasted 26ms with no metric checked in this forensics pass showing renewed pending.

## 11-12. Pending-instant state (Gateway / Reactor / Mock)

At `t=1787921346` (the pending sample): `gateway_active_requests=11`, `active_connections=1`
(pool had not yet finished ramping). One second later: `active_connections=400`,
`gateway_active_requests=400`, `pending=0` (never nonzero again). Mock's own request log
independently corroborates live arrival: 342 `"stream requested"` log lines in the wall-clock
second before the pending sample, 58 more in the same second as the sample (342+58=400, plus the
5 earlier prewarm = 405 total, matching k6's `http_reqs.count=405` exactly). **Not** a
zero-live-traffic phantom signal — real concurrent request pressure was in flight at that instant.

## 13. ConnectionProvider capacity arithmetic

`active_connections` peaked at exactly 400 (never exceeded, never approached
`WEBCLIENT_MAX_CONNECTIONS=800`). This was not an 800-ceiling event — "ConnectionProvider capacity
limit" is not an applicable description here.

## 14. Client completed/failed

Unchanged from the original report: `completed=400/400`, `rejected=0`, `failed_mid_stream=0`,
`failed_no_event=0`.

## 15. TTFC / duration percentiles (p95/max; p99 not exported by this k6 config)

`client_completed_ttfc_seconds`: p95=1.09005s, max=1.106s.
`client_completed_stream_duration_seconds`: p95=8.08205s, max=8.095s (min 7.885s — all 400
requests completed within a 210ms band). k6's `--summary-export` here does not include p99, but
max is a strict upper bound and sits only ~13ms above p95 — there is no basis in the data for a
long-tail request waiting 20-30s; see `timeline.md` §"Client-side outcome check."

## 16. Request-lifecycle correlation

See §11-12 — reconstructed via Gateway's own `gateway_active_requests` gauge plus Mock's access
log timestamps (no per-request correlation ID cross-referencing was available/needed; the
aggregate counts already reconcile exactly).

## 17. FD/OS evidence

`fd_open` 47 → 80 → 846 across the ramp, `max_files=1,048,576` (846/1,048,576 ≈ 0.08% — far below
Unit 3's 85% safety threshold). Zero `EMFILE`/`EADDRNOTAVAIL`/connection-refused/reset lines in
either `gateway.log` or `mock.log`. The only ERROR line in `gateway.log` is the well-known
macOS-native-DNS-resolver Netty warning at process startup (`MacOSDnsServerAddressStreamProviders`),
unrelated to localhost connection pooling. No OS/FD resource issue is a candidate cause.

## 18-19. Verdict

The forensics brief asks for exactly one of Case A / B / C. The evidence does not collapse
cleanly into one:

- **On the specific event**: every Case-A sub-condition except one holds — wave window (yes,
  +0.09s), live user request active (yes, `gateway_active_requests=11` and corroborating Mock log
  arrivals), correct Reactor semantic (yes, genuine acquire-wait, bytecode-confirmed), correct
  label (yes, single clean series). The one sub-condition that does **not** hold as literally
  specified is "collector window correct" — the query window is process-lifetime, not
  wave-scoped, even though in this instance the sample happened to land inside the wave anyway.
- **On the validity rule itself**: a genuine, evidence-backed gap exists between the shipped
  collector (`peak > 0`, whole-process-lifetime window) and the documented design intent
  (`phase4-design.md` §7-1: "지속적으로 0보다 크면" — sustained, wave-scoped). This is not the
  literal Case-B profile described in the brief (pending confined to prewarm/drain with wave=0) —
  the wave itself did have the nonzero sample — but it independently satisfies Case B's other
  named symptom ("collector가 process lifetime peak를 사용").

**Reported as a hybrid finding, not forced into a single letter**: the underlying physical event
is real, in-wave, and traffic-correlated (substantively Case A for this data point); separately,
the validity-check implementation diverges from its own documented design intent in both
threshold (single-sample vs. sustained) and window scope (process-lifetime vs. wave-only), which
is an amendment-proposal-worthy issue independent of this specific run's outcome. No fix has been
applied to any frozen file.

## 20. Current M3 N=400 validity status

**Unchanged — still INVALID** (`m3_pending_no_anomaly`). This forensics pass does not alter the
recorded artifact or its validity determination; it only explains what produced it. Per the
forensics brief §15, a result is not to be reclassified based on a single instance without a
reviewed and approved rule amendment.

## 21. Contract amendment — recommended for user review (not applied)

Two independent, narrowly-scoped candidate amendments surfaced by this forensics pass (presented
for decision, not applied):

1. Scope the pending-anomaly Prometheus query window to the canonical wave only (roughly
   `client_wave_start_epoch_ms` min to `k6_wall_end_ms`), excluding prewarm/startup/drain, to
   match `phase4-design.md` §7-1's "Primary Normal workload 실행 중" language.
2. Define "anomaly" as sustained/multi-sample pending>0 (matching "지속적으로") rather than a
   single-sample peak>0, to match the same design sentence's "지속적으로" wording, while still
   catching a genuinely stuck/at-capacity pool (which would show multiple consecutive nonzero
   samples, not one).

Both would need explicit approval, a regression pass, and an explicit decision on how much of the
already-collected N=50-400 canonical data needs re-validation/re-collection under the amended
rule before Unit 5 could resume under it.

## 22. Additional diagnostic load needed?

Not required to resolve this specific question — the existing artifact plus Reactor Netty
bytecode fully account for the single sample. If the user wants to *positively confirm* that
ramp-up-instant pending is reproducible/expected behavior (rather than inferred from one
occurrence), a possible future diagnostic would be a repeat M3 N=400 run solely to observe whether
a similar single-sample pending recurs at the same relative wave offset — but this would be new
load and is explicitly out of scope for this forensics pass.

## 23. Unit 5 resume status

**Not resumed.** Per the governing instructions: M3 N=400 not retried, N=640 not run, M1/M2
refinement not run, no validity-rule/threshold/config change made, `unit5-closed-screening/`
artifacts untouched. Awaiting user decision on the amendment proposal (§21) and on how to treat
the current M3 N=400 point before any further Unit 5 progression.
