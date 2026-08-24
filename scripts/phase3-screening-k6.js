// Phase 3 Unit 6 Screening — Constant Arrival Rate (Open Model).
// Phase-3-specific copy of load-test-k6/scenarios/02-constant-arrival-rate.js's approach
// (Phase 2 asset, not modified — docs/test-plan/phase3-design.md §11 requires a separate script).
// Same outcome-tagged client metric design (TTFC/stream-duration recorded only after each
// iteration's outcome is known) — see docs/decisions/timeout-semantics.md.
//
// One addition vs. the Phase 2 script, carried over from the Unit 5.5 D2 diagnostic finding
// (docs/test-results/phase3/unit5.5-diagnostics/SUMMARY.md): the 'error' handler calls
// client.close() so the control loop cannot hang if the Gateway aborts the connection
// (admission reject on some SSE paths, timeout, upstream_error) mid-iteration.
import sse from 'k6/x/sse';
import { Trend, Counter } from 'k6/metrics';

const RATE = Number(__ENV.RATE || 5);
const DURATION = __ENV.DURATION || '25s';
const PRE_ALLOCATED_VUS = Number(__ENV.PRE_ALLOCATED_VUS || 100);
const MAX_VUS = Number(__ENV.MAX_VUS || 400);

export const options = {
    scenarios: {
        arrival: {
            executor: 'constant-arrival-rate',
            rate: RATE,
            timeUnit: '1s',
            duration: DURATION,
            preAllocatedVUs: PRE_ALLOCATED_VUS,
            maxVUs: MAX_VUS,
            gracefulStop: __ENV.GRACEFUL_STOP || '90s',
        },
    },
};

export const client_ttfc_seconds = new Trend('client_ttfc_seconds');
export const client_stream_duration_seconds = new Trend('client_stream_duration_seconds');
export const client_ttfc_completed_seconds = new Trend('client_ttfc_completed_seconds');

export const client_completed_total = new Counter('client_completed_total');
export const client_rejected_total = new Counter('client_rejected_total');
export const client_failed_mid_stream_total = new Counter('client_failed_mid_stream_total');
export const client_failed_no_event_total = new Counter('client_failed_no_event_total');
export const client_events_received_total = new Counter('client_events_received_total');

const GATEWAY_URL = __ENV.GATEWAY_URL || 'http://127.0.0.1:8080/chat/stream';
const CHUNK_COUNT = Number(__ENV.CHUNK_COUNT || 35);
const CHUNK_INTERVAL_MS = Number(__ENV.CHUNK_INTERVAL_MS || 200);
const FIRST_CHUNK_DELAY_MS = Number(__ENV.FIRST_CHUNK_DELAY_MS || 1000);
const CHUNK_SIZE_BYTES = Number(__ENV.CHUNK_SIZE_BYTES || 64);

export default function () {
    const start = Date.now();
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
                client_events_received_total.add(1);
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
