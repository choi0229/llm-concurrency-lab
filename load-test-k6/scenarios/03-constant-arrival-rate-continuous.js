// Scenario 3 — Constant Arrival Rate, Continuous Warm-up -> Measurement
// (Phase 2 Formal only).
//
// Forked from 02-constant-arrival-rate.js instead of modifying it in place:
// Phase 1's `scripts/run-formal-benchmark.sh` still invokes 02 as two SEPARATE
// k6 processes (warm-up run to completion and discarded, full drain, then a
// fresh measurement-only k6 process) and Phase 1's results are already
// finalized (docs/test-results/phase1/phase1-final-report.md) — that file
// must not change behavior under it, even accidentally.
//
// Phase 2 Formal (docs/decisions/phase2-formal-linux-environment.md, item 2)
// found a structural bias in the two-process design: because the measurement
// k6 process starts against a fully drained (empty) system, the ~7.9s-long
// mock stream means no request can complete until ~7.9s into the window, so
// `measurement_window_completion_rate` is biased below the true arrival rate
// even when the environment is perfectly healthy — independent of any
// environment stall.
//
// This scenario fixes that at the source: ONE continuous k6 process runs
// warm-up immediately followed by measurement, at the same RATE, with NO
// drain in between — so the in-flight population carried over from warm-up
// keeps the measurement window's completion curve at steady state from its
// first second, not empty. `setup()` captures the phase boundary once, in
// the same Docker-VM clock domain the iterations run in (matches this
// project's existing "nominal boundary computed from a clock read, not
// observed after the fact" convention — see the MEASUREMENT_END comment in
// scripts/run-phase2-formal-benchmark.sh).
//
// Warm-up-phase iterations still send real requests (keeping the Gateway JVM
// AND this k6 process's own VU/connection pool warm) but are excluded from
// every Formal client-side metric — docs/test-plan/phase2-formal-protocol.md
// §3. `measurement_iterations_started_total` (a custom Counter) replaces k6's
// built-in `iterations` metric as the Formal cohort-size source, because the
// built-in metric counts BOTH phases and can't be filtered after the fact.
//
// Phase-boundary single source of truth (2026-08-17, Unit 5.6): the shell
// harness used to independently recompute the same boundary via its own
// `vm_epoch()` clock read (RUN_START + WARMUP_SEC) for its Prometheus
// queries — a SECOND clock read, taken at a slightly different moment (after
// this k6 container's own `docker run` startup latency) than the one this
// script's setup() takes. Both reads happen on the same underlying VM/host
// clock (0 drift already verified in preflight), so there was no real
// clock-domain skew, only container-launch-latency skew between the two read
// times — but it was still two independent computations of "the same"
// number. Fixed by making this script's own setup() read the ONE and ONLY
// source: it is emitted as a Gauge (`measurement_start_epoch_s`) so the shell
// harness reads it back out of this run's own `--summary-export` JSON
// *after* the process exits, instead of computing its own value in advance.
import sse from 'k6/x/sse';
import { Trend, Counter, Gauge } from 'k6/metrics';

const RATE = Number(__ENV.RATE || 5);
const WARMUP_SEC = Number(__ENV.WARMUP_SEC || 0);
const MEASUREMENT_SEC = Number(__ENV.MEASUREMENT_SEC || 20);
const DURATION = `${WARMUP_SEC + MEASUREMENT_SEC}s`;
const PRE_ALLOCATED_VUS = Number(__ENV.PRE_ALLOCATED_VUS || 50);
const MAX_VUS = Number(__ENV.MAX_VUS || 300);

export const options = {
    scenarios: {
        arrival: {
            executor: 'constant-arrival-rate',
            rate: RATE,
            timeUnit: '1s',
            duration: DURATION,
            preAllocatedVUs: PRE_ALLOCATED_VUS,
            maxVUs: MAX_VUS,
            // Long enough for an iteration that arrived right at the end of
            // the arrival phase to still run out its full CHAT_TOTAL_TIMEOUT_MS
            // budget rather than being force-interrupted.
            gracefulStop: __ENV.GRACEFUL_STOP || '90s',
        },
    },
};

// setup() runs exactly once, before any VU iteration starts — the ONE clock
// read that defines the phase boundary for this entire run (see file header).
export const measurement_start_epoch_s = new Gauge('measurement_start_epoch_s');

export function setup() {
    const measurementStartMs = Date.now() + WARMUP_SEC * 1000;
    measurement_start_epoch_s.add(measurementStartMs / 1000);
    return { measurementStartMs };
}

// Outcome-tagged (outcome: 'completed' | 'rejected' | 'failed_mid_stream' | 'failed_no_event').
// All of the below are recorded for the measurement phase only.
export const client_ttfc_seconds = new Trend('client_ttfc_seconds');
export const client_stream_duration_seconds = new Trend('client_stream_duration_seconds');
export const client_ttfc_completed_seconds = new Trend('client_ttfc_completed_seconds');

export const client_completed_total = new Counter('client_completed_total');
export const client_rejected_total = new Counter('client_rejected_total');
export const client_failed_mid_stream_total = new Counter('client_failed_mid_stream_total');
export const client_failed_no_event_total = new Counter('client_failed_no_event_total');
export const client_events_received_total = new Counter('client_events_received_total');
// Formal cohort-size source of truth for this scenario — see file header.
export const measurement_iterations_started_total = new Counter('measurement_iterations_started_total');

const GATEWAY_URL = __ENV.GATEWAY_URL || 'http://gateway:8080/chat/stream';
const CHUNK_COUNT = Number(__ENV.CHUNK_COUNT || 35);
const CHUNK_INTERVAL_MS = Number(__ENV.CHUNK_INTERVAL_MS || 200);
const FIRST_CHUNK_DELAY_MS = Number(__ENV.FIRST_CHUNK_DELAY_MS || 1000);

export default function (data) {
    const start = Date.now();
    // Phase is decided by ARRIVAL time (before the request is even sent) —
    // matches this project's existing outcome-cohort definition (protocol
    // §4-B: "measurement_start ≤ 요청 시작 시각 < measurement_end"), just
    // applied against the warm-up/measurement boundary too, so an iteration
    // that starts in warm-up but finishes after the boundary is still
    // correctly excluded, and one that starts right at the boundary is
    // correctly included even though it won't finish until later.
    const phase = (data && start >= data.measurementStartMs) ? 'measurement' : 'warmup';
    if (phase === 'measurement') {
        measurement_iterations_started_total.add(1);
    }

    let firstEventAt = null;
    let eventCount = 0;
    let sawFinal = false;

    const body = JSON.stringify({
        firstChunkDelayMs: FIRST_CHUNK_DELAY_MS,
        chunkIntervalMs: CHUNK_INTERVAL_MS,
        chunkCount: CHUNK_COUNT,
        chunkSizeBytes: 64,
    });

    const res = sse.open(
        GATEWAY_URL,
        { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body },
        function (client) {
            client.on('event', function (event) {
                eventCount++;
                if (phase === 'measurement') {
                    client_events_received_total.add(1);
                }
                if (firstEventAt === null) {
                    firstEventAt = Date.now();
                }
                if (event.name === 'final' || (event.data && event.data.indexOf('COMPLETED') !== -1)) {
                    sawFinal = true;
                }
            });
            client.on('error', function (e) {
                console.log('sse error: ' + JSON.stringify(e));
            });
        }
    );

    const durationSeconds = (Date.now() - start) / 1000;

    // Warm-up traffic is real (keeps Gateway/JVM and this VU's connection
    // warm) but is never recorded into a Formal client-side metric.
    if (phase !== 'measurement') {
        return;
    }

    let outcome;
    if (res && res.status === 503) {
        outcome = 'rejected';
        client_rejected_total.add(1);
    } else if (sawFinal) {
        outcome = 'completed';
        client_completed_total.add(1);
    } else if (eventCount > 0) {
        outcome = 'failed_mid_stream';
        client_failed_mid_stream_total.add(1);
    } else {
        outcome = 'failed_no_event';
        client_failed_no_event_total.add(1);
    }

    if (firstEventAt !== null) {
        const ttfc = (firstEventAt - start) / 1000;
        client_ttfc_seconds.add(ttfc, { outcome: outcome });
        if (outcome === 'completed') {
            client_ttfc_completed_seconds.add(ttfc);
        }
    }
    client_stream_duration_seconds.add(durationSeconds, { outcome: outcome });
}
