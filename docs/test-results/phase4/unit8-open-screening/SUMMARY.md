# Phase 4 Open Unit 8 — Canonical Screening (open-final-client-lifecycle-v1)

**STOPPED per design at M2 R=256 (`UNCLASSIFIED_MODEL_FAILURE`)** — the driver's UNEXPLAINED
full-STOP logic (approved Unit 7.1, verified intact Unit 7.5) fired correctly and did **not**
proceed to M3 R=256. Screening is **not complete**. No MSAR refinement, no Open Formal.

## Tooling note (disclosed, not a finding)

The first launch attempt failed immediately (`harness_no_output_dir`, auto-retried once, still
failed) because `scripts/run-phase4-open-benchmark.sh`'s hardcoded `OUT_DIR` still pointed at
`unit7-open-harness/` while the driver's `HARNESS_OUT_ROOT` had been updated to `unit8-open-harness/`
— a path-sync mistake made when preparing this Unit, not a measurement finding. Fixed immediately
(`OUT_DIR` updated to match); the two resulting empty/invalid `m1-r2-screen1(-retry1)` directories
under `unit7-open-harness/` are left in place as harmless leftovers from the misconfigured first
attempt, not reused. The driver was restarted clean immediately after the fix.

## Geometric progression achieved before STOP

| Run | Valid | Rate | Dropped | Completion ratio | Color | Classification |
|---|---|---|---|---|---|---|
| m1-r2-screen1 | ✓ | 2 | 0 | 1.000 | GREEN | GREEN |
| m1-r4-screen1 | ✓ | 4 | 0 | 1.000 | GREEN | GREEN |
| m1-r10-screen1 | ✓ | 10 | 0 | 0.291 | RED | MODEL_REJECTION |
| m2-r2-screen1 | ✓ | 2 | 0 | 1.000 | GREEN | GREEN |
| m2-r4-screen1 | ✓ | 4 | 0 | 1.000 | GREEN | GREEN |
| m2-r10-screen1 | ✓ | 10 | 0 | 1.000 | GREEN | GREEN |
| m2-r20-screen1 | ✓ | 20 | 0 | 1.000 | GREEN | GREEN |
| m2-r40-screen1 | ✓ | 40 | 0 | 1.000 | GREEN | GREEN |
| m2-r80-screen1 | ✓ | 80 | 0 | 1.000 | GREEN | GREEN |
| m2-r160-screen1 | ✓ | 160 | 0 | 1.000 | GREEN | GREEN |
| **m2-r256-screen1** | ✓ | 256 | 0 | **0.815** | **RED** | **UNCLASSIFIED_MODEL_FAILURE (STOP)** |
| m3-r2-screen1 | ✓ | 2 | 0 | 1.000 | GREEN | GREEN |
| m3-r4-screen1 | ✓ | 4 | 0 | 1.000 | GREEN | GREEN |
| m3-r10-screen1 | ✓ | 10 | 0 | 1.000 | GREEN | GREEN |
| m3-r20-screen1 | ✓ | 20 | 0 | 1.000 | GREEN | GREEN |
| m3-r40-screen1 | ✓ | 40 | 0 | 1.000 | GREEN | GREEN |
| m3-r80-screen1 | ✓ | 80 | 0 | 1.000 | GREEN | GREEN |

M1 correctly deactivated after its known-RED at R=10 (`MODEL_REJECTION`) — matches Unit 7.3/7.5
regressions closely (`rejected` count in the same range each time), a reproducible genuine finding,
not re-litigated here. M3 never reached R=256 (driver stopped one level early, exactly as designed).
**Every point through R=160 for both M2 and M3 is `dropped_iterations=0`, `completion_ratio=1.000`
GREEN** — the Unit 7.5 client-lifecycle fix holds cleanly in the real canonical-Screening context,
not just in isolated regression runs (M2 R=160 here: Tomcat connections peak **1279/3200**, fully
non-binding).

## The R=256 finding: investigated, not yet classified

`m2-r256-screen1`: `dropped_iterations=0` (loadgen delivered the full offered rate),
`completion_ratio=0.815`, `failed_no_event=5688` out of 30721 started. Tomcat
`tomcat_connections_non_binding=True` (peak **2092**, well under the configured 3200 ceiling) — this
is **not** a `CONTROL_CONFIG_INVALID` in the Tomcat-connector sense (§16 of the governing
instruction); the configured connector cap is not what's binding.

**gateway.log** contains 9 occurrences of `java.net.BindException: Can't assign requested address`
in `BlockingMockLlmRelay` — the Gateway's **own outbound** connection to Mock hitting the same class
of OS-level `EADDRNOTAVAIL` error found in Units 7.2–7.4, but now on the Gateway→Mock leg, not the
k6→Gateway leg. Source-confirmed cause: `MockLlmClient.openStream()` uses raw
`java.net.HttpURLConnection` (deliberately, per its own javadoc, to keep M1/M2's outbound path
identical), and `BlockingMockLlmRelay.relay()`'s `finally` block **unconditionally calls
`connection.disconnect()`** after every single request — forcibly tearing down the connection
(bypassing the JVM's `KeepAliveCache` reuse mechanism) rather than reusing it, meaning the Gateway
becomes the active closer for *every* outbound request to Mock, generating its own TIME_WAIT churn on
the same shared host. This is intentionally identical M1/M2 behavior (not an M2-specific bug), and
would equally affect M3 only in the sense that M3 uses a completely different, pooling-aware Reactor
Netty `WebClient` for its own outbound calls — not the same code path at all.

However: **the 9 `BindException` occurrences (`upstream_error=9` in `server_outcomes_whole_process_
estimate`) explain only ~0.16% of the 5688 client-side failures** — nowhere near the dominant cause.
`k6-stdout.log` shows **zero** `sse error` console lines (the `client.on('error', ...)` callback never
fired for any of the 5688 failures) and `vus.max=2096` (comfortably under `PRE_ALLOCATED_VUS=15360`,
so not a k6 VU-pool issue). This is the exact signature already characterized in Units 7.2/7.4 for a
connect-stage failure on the **k6→Gateway leg**: the setup callback never runs at all, so nothing
JS-visible fires, consistent with the same `dial tcp ...` class of client-side connection-
establishment failure recurring — just now at R=256 rather than R=160.

**Grepped every other completed run's `gateway.log` in this Screening for `BindException`: zero
matches anywhere except `m2-r256-screen1` (9 occurrences).** This is a genuine, reproducible
threshold effect specific to the top of the frozen rate range, not noise and not present at any
lower rate including R=160.

**Working hypothesis (not yet adopted as a classification):** all three benchmark processes (k6,
Gateway, Mock) run on one shared host and draw from the *same* finite ephemeral-port pool
(16384, `sysctl net.inet.ip.portrange`). The Unit 7.5 fix addressed the k6→Gateway leg's connection
residency (successfully, confirmed clean through R=160 here). It did not address — because it was
not yet known to matter — the Gateway→Mock leg's own connection churn (`disconnect()`-per-request,
confirmed via source read above). At R=160 the combined ephemeral-port demand from both legs
evidently stayed within the shared budget; at R=256 (1.6× higher, and now also carrying the
Gateway-outbound leg's own contribution) it appears to tip over for a share of connections on the
k6→Gateway leg specifically. This has **not** been independently confirmed via socket telemetry for
this specific run (the canonical harness does not run the socket sampler — that instrumentation
exists only in the Unit 7.2 diagnostic harness) — flagged as a gap, not filled with an assumption.

## Why this is not classified here

Per this project's standing rule (applied without exception since Unit 5.2's first pending-thread
anomaly): an unexplained finding is investigated and reported, not silently reclassified or pushed
through. The evidence here strongly points to a shared-host resource-contention effect specific to
R=256 rather than a Virtual Thread execution-capacity finding, but adopting `CONTROL_CONFIG_INVALID`,
a new RED subtype, or any other disposition without the user's decision would repeat exactly the
mistake corrected in Units 7.1→7.4 (accepting a plausible-sounding explanation without confirming it,
and without the user weighing in on how a genuinely novel signal should be classified going forward).

## Immediate state

All processes/ports clean-shutdown by the driver itself (confirmed: ports 8000/18101/18102/18103/
9094 free, no stale k6/harness/sampler processes). No files deleted. `screening-summary.json` /
`driver-log.txt` reflect the STOP accurately. Canonical epoch metadata (`screening_epoch:
"open-final-client-lifecycle-v1"`) is recorded in every run's `environment.json` and in
`screening-summary.json`.
