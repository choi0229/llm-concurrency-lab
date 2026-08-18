// Scenario 1 — Concurrent SSE Connections (Closed Model).
// N VUs each open exactly one long-lived SSE stream through the Gateway,
// all starting together (shared-iterations executor with vus == iterations
// starts every VU immediately, no ramp-up) — the goal is to hold N
// concurrent long-lived connections, not to generate a particular RPS.
//
// See docs/decisions/load-generator.md for why this uses k6/x/sse instead
// of core http, and its version pin.
import sse from 'k6/x/sse';
import { Trend, Counter } from 'k6/metrics';

const VUS = Number(__ENV.VUS || 5);

export const options = {
    vus: VUS,
    iterations: VUS,
    maxDuration: __ENV.MAX_DURATION || '2m',
};

export const client_ttfc_seconds = new Trend('client_ttfc_seconds');
export const client_stream_duration_seconds = new Trend('client_stream_duration_seconds');
export const client_stream_completed_total = new Counter('client_stream_completed_total');
export const client_stream_failed_total = new Counter('client_stream_failed_total');
export const client_events_received_total = new Counter('client_events_received_total');

const GATEWAY_URL = __ENV.GATEWAY_URL || 'http://gateway:8080/chat/stream';
const CHUNK_COUNT = Number(__ENV.CHUNK_COUNT || 20);
const CHUNK_INTERVAL_MS = Number(__ENV.CHUNK_INTERVAL_MS || 200);
const FIRST_CHUNK_DELAY_MS = Number(__ENV.FIRST_CHUNK_DELAY_MS || 1000);

export default function () {
    const start = Date.now();
    let firstEventAt = null;
    let sawFinal = false;

    const body = JSON.stringify({
        firstChunkDelayMs: FIRST_CHUNK_DELAY_MS,
        chunkIntervalMs: CHUNK_INTERVAL_MS,
        chunkCount: CHUNK_COUNT,
        chunkSizeBytes: 64,
    });

    sse.open(
        GATEWAY_URL,
        { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body },
        function (client) {
            client.on('event', function (event) {
                client_events_received_total.add(1);
                if (firstEventAt === null) {
                    firstEventAt = Date.now();
                    client_ttfc_seconds.add((firstEventAt - start) / 1000);
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

    client_stream_duration_seconds.add((Date.now() - start) / 1000);
    if (sawFinal) {
        client_stream_completed_total.add(1);
    } else {
        client_stream_failed_total.add(1);
    }
}
