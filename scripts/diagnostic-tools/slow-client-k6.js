// Unit 5.5 D2 diagnostic — deliberately slow SSE client using xk6-sse's synchronous JS-callback
// design. Confirmed by reading the actual xk6-sse v0.1.11 source
// (docs/test-results/phase3/unit5.5-diagnostics/slow-client/capability-audit.md): the reader
// goroutine sends each parsed event on an UNBUFFERED channel and cannot read the next bytes off
// the TCP socket until this script's event callback returns -- so sleeping here creates genuine
// TCP-level backpressure, not just slow JS-side processing.
//
// NOT a load test: 1 VU, 1 iteration. Workload is intentionally large/fast-produced (Mock LLM
// config below) specifically so there is enough in-flight data for a slow reader to matter --
// same principle for both targets (Unit 5.5 §10 -- "조건은 P3-B/P3-C 동일하게 사용한다").
import sse from 'k6/x/sse';
import { sleep } from 'k6';
import { Counter } from 'k6/metrics';

const eventsReceived = new Counter('slow_client_events_received_total');
const bytesReceived = new Counter('slow_client_bytes_received_total');

const TARGET_URL = __ENV.TARGET_URL || 'http://127.0.0.1:8082/chat/stream';
const SLEEP_PER_EVENT_SECONDS = Number(__ENV.SLEEP_PER_EVENT_SECONDS || '0.15');
const CHUNK_COUNT = Number(__ENV.CHUNK_COUNT || '60');
const CHUNK_SIZE_BYTES = Number(__ENV.CHUNK_SIZE_BYTES || '65536');

export default function () {
    const body = JSON.stringify({
        firstChunkDelayMs: 0,
        chunkIntervalMs: 0,
        chunkCount: CHUNK_COUNT,
        chunkSizeBytes: CHUNK_SIZE_BYTES,
    });
    const params = { headers: { 'Content-Type': 'application/json' } };

    const start = Date.now();
    let eventCount = 0;

    sse.open(TARGET_URL, Object.assign({ method: 'POST', body: body }, params), function (client) {
        client.on('event', function (event) {
            eventCount++;
            eventsReceived.add(1);
            if (event.data) {
                bytesReceived.add(event.data.length);
            }
            // The deliberate slow-read: this callback runs synchronously in xk6-sse's single
            // control-loop goroutine, blocking the next socket read until it returns.
            sleep(SLEEP_PER_EVENT_SECONDS);
        });
        client.on('error', function (e) {
            // xk6-sse's control loop does not auto-terminate on an error signal (confirmed by
            // reading sse.go -- see capability-audit.md); without an explicit close() here the
            // script hangs until k6's own graceful-stop timeout once the server aborts the
            // connection (e.g. on the Gateway's own write_overflow fail-fast).
            console.log('sse error (closing client): ' + JSON.stringify(e));
            client.close();
        });
    });

    console.log('slow-client done: events=' + eventCount + ' elapsedMs=' + (Date.now() - start));
}
