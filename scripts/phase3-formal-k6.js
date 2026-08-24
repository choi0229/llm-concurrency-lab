// Phase 3 Formal — Constant Arrival Rate, Continuous Warm-up -> Measurement (Unit 6.5).
//
// Phase-3-specific fork of the technique in Phase 2's
// load-test-k6/scenarios/03-constant-arrival-rate-continuous.js (read, not modified or imported —
// that file is a finalized Phase 2 asset). Same reason for existing: a separate
// warm-up-run-to-completion -> drain -> fresh measurement-only k6 process design starts the
// measurement window against an empty system, and since the Normal Mock workload takes ~7.8s to
// complete, no request can complete until ~7.8s into the window -- a structural under-count of
// completion throughput, independent of any real capacity signal (docs/decisions/
// phase2-formal-linux-environment.md item 2). This script runs warm-up immediately followed by
// measurement in ONE continuous k6 process, same RATE throughout, no drain in between.
//
// Single-clock-read rule (mirrors Phase 2's 2026-08-17 Unit 5.6 fix): setup() reads Date.now()
// exactly once and emits measurementStartMs as a Gauge (measurement_start_epoch_s) so the shell
// harness reads this run's own summary-export JSON for the authoritative boundary instead of
// independently recomputing "process_start + WARMUP_SEC" with a second, slightly-later clock read.
//
// Client cohort (docs/test-plan/phase3-formal-protocol.md Sec 12-B): measurement_iterations_started_total
// (not k6's built-in `iterations`, which can't be filtered to one phase after the fact) is the
// Formal cohort-size source of truth. Phase classification is by ARRIVAL time, not completion time.
//
// Unit 5.5 D2 carry-over (docs/test-results/phase3/unit5.5-diagnostics/SUMMARY.md): the 'error'
// handler calls client.close() so the control loop cannot hang if the Gateway aborts the
// connection mid-iteration (same fix already applied in scripts/phase3-screening-k6.js).
import sse from 'k6/x/sse';
import { Trend, Counter, Gauge } from 'k6/metrics';

const RATE = Number(__ENV.RATE || 5);
const WARMUP_SEC = Number(__ENV.WARMUP_SEC || 120);
const MEASUREMENT_SEC = Number(__ENV.MEASUREMENT_SEC || 300);
const DURATION = `${WARMUP_SEC + MEASUREMENT_SEC}s`;
const PRE_ALLOCATED_VUS = Number(__ENV.PRE_ALLOCATED_VUS || 200);
const MAX_VUS = Number(__ENV.MAX_VUS || 800);

export const options = {
    scenarios: {
        arrival: {
            executor: 'constant-arrival-rate',
            rate: RATE,
            timeUnit: '1s',
            duration: DURATION,
            preAllocatedVUs: PRE_ALLOCATED_VUS,
            maxVUs: MAX_VUS,
            // Long enough for an iteration that arrived right at the end of the arrival phase to
            // still run out its full CHAT_TOTAL_TIMEOUT_MS budget rather than being
            // force-interrupted (a k6-side "interrupted" outcome, distinct from a Gateway outcome).
            gracefulStop: __ENV.GRACEFUL_STOP || '90s',
        },
    },
};

// setup() runs exactly once, before any VU iteration starts -- the ONE clock read that defines
// the warm-up/measurement boundary for this entire run (see file header).
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
// Formal cohort-size source of truth (docs/test-plan/phase3-formal-protocol.md Sec 12-B).
export const measurement_iterations_started_total = new Counter('measurement_iterations_started_total');

const GATEWAY_URL = __ENV.GATEWAY_URL || 'http://127.0.0.1:8080/chat/stream';
const CHUNK_COUNT = Number(__ENV.CHUNK_COUNT || 35);
const CHUNK_INTERVAL_MS = Number(__ENV.CHUNK_INTERVAL_MS || 200);
const FIRST_CHUNK_DELAY_MS = Number(__ENV.FIRST_CHUNK_DELAY_MS || 1000);
const CHUNK_SIZE_BYTES = Number(__ENV.CHUNK_SIZE_BYTES || 64);

export default function (data) {
    const start = Date.now();
    // Phase decided by ARRIVAL time (before the request is even sent) -- matches the outcome-
    // cohort definition (protocol Sec 12-B): an iteration that starts in warm-up but finishes
    // after the boundary is still excluded; one that starts right at the boundary is included
    // even though it won't finish until later.
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
        chunkSizeBytes: CHUNK_SIZE_BYTES,
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
                client.close();
            });
        }
    );

    const durationSeconds = (Date.now() - start) / 1000;

    // Warm-up traffic is real (keeps the Gateway JVM and this VU's own connection warm) but is
    // never recorded into a Formal client-side metric.
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
