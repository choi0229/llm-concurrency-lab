import sse from 'k6/x/sse';
import { Trend, Counter } from 'k6/metrics';

const ttfc = new Trend('client_ttfc_seconds');
const eventsReceived = new Counter('client_events_received_total');
const streamCompleted = new Counter('client_stream_completed_total');
const disconnected = new Counter('client_intentional_disconnect_total');

const MOCK_LLM_URL = __ENV.MOCK_LLM_URL || 'http://mockllm:8000/mock/stream';

export default function () {
    const start = Date.now();
    let firstEventAt = null;
    let eventTimestamps = [];
    let eventCount = 0;

    const params = { headers: { 'Content-Type': 'application/json' } };
    const body = JSON.stringify({
        firstChunkDelayMs: 100,
        chunkIntervalMs: 200,
        chunkCount: 6,
        chunkSizeBytes: 16,
    });

    sse.open(MOCK_LLM_URL, Object.assign({ method: 'POST', body: body }, params), function (client) {
        client.on('event', function (event) {
            eventCount++;
            eventTimestamps.push(Date.now() - start);
            if (firstEventAt === null) {
                firstEventAt = Date.now();
                ttfc.add((firstEventAt - start) / 1000);
            }
            eventsReceived.add(1);

            // Intentional disconnect after the 3rd event to prove close() actually
            // tears down the connection mid-stream (verified against mock-llm's
            // own cancelled_requests counter separately in Unit 4-4).
            if (eventCount === 3) {
                disconnected.add(1);
                client.close();
                return;
            }

            if (event.name === 'final' || (event.data && event.data.indexOf('COMPLETED') !== -1)) {
                streamCompleted.add(1);
            }
        });

        client.on('error', function (e) {
            console.log('sse error: ' + JSON.stringify(e));
        });
    });

    console.log('event timestamps (ms from start): ' + JSON.stringify(eventTimestamps));
}
