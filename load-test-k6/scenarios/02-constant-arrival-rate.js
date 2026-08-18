// Scenario 2 — Constant Arrival Rate (Open Model).
// New iterations start at a fixed rate regardless of how long prior
// iterations are still taking — this is what actually distinguishes it from
// Scenario 1's closed model: if the Gateway falls behind, arrivals keep
// coming and a backlog builds up rather than the load generator self-throttling.
//
// Outcome-tagged client metrics (per Unit 4-2 review): TTFC and stream
// duration are recorded ONLY after each iteration's final outcome is known,
// tagged with that outcome, so a single blended average never hides a
// success population's TTFC behind a much worse failure population's (or
// vice versa) — see docs/decisions/timeout-semantics.md.
import sse from 'k6/x/sse';
import { Trend, Counter } from 'k6/metrics';

const RATE = Number(__ENV.RATE || 5);
const DURATION = __ENV.DURATION || '20s';
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
            // budget rather than being force-interrupted (which would show up
            // as a k6-side "interrupted" outcome, a confound distinct from the
            // Gateway's own timeout/reject/stale outcomes).
            gracefulStop: __ENV.GRACEFUL_STOP || '90s',
        },
    },
};

// Outcome-tagged (outcome: 'completed' | 'rejected' | 'failed_mid_stream' | 'failed_no_event').
export const client_ttfc_seconds = new Trend('client_ttfc_seconds');
export const client_stream_duration_seconds = new Trend('client_stream_duration_seconds');
// Convenience un-tagged Trend for the success-only population specifically,
// so a top-line "TTFC(success only)" figure doesn't require tag-filtering tooling.
export const client_ttfc_completed_seconds = new Trend('client_ttfc_completed_seconds');

export const client_completed_total = new Counter('client_completed_total');
export const client_rejected_total = new Counter('client_rejected_total');
export const client_failed_mid_stream_total = new Counter('client_failed_mid_stream_total');
export const client_failed_no_event_total = new Counter('client_failed_no_event_total');
export const client_events_received_total = new Counter('client_events_received_total');

const GATEWAY_URL = __ENV.GATEWAY_URL || 'http://gateway:8080/chat/stream';
const CHUNK_COUNT = Number(__ENV.CHUNK_COUNT || 35);
const CHUNK_INTERVAL_MS = Number(__ENV.CHUNK_INTERVAL_MS || 200);
const FIRST_CHUNK_DELAY_MS = Number(__ENV.FIRST_CHUNK_DELAY_MS || 1000);

export default function () {
    const start = Date.now();
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
                client_events_received_total.add(1);
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
