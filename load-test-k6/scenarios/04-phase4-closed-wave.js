// Phase 4 Closed-model concurrent wave — new script, does not modify 01-concurrent-connections.js
// (Phase 1-3 frozen results depend on that file unchanged).
//
// Adds two things 01 does not have, needed for Phase 4 Unit 4 harness verification
// (docs/test-plan/phase4-closed-harness-protocol.md):
//   1. A small sequential prewarm phase (setup(), single VU, blocking calls) to remove JIT/
//      first-connection cold-start effects from the canonical wave population — prewarm traffic
//      is real (hits the Gateway) but is never counted in any wave metric.
//   2. Per-iteration wave-start timestamp (client_wave_start_epoch_ms) so the harness can compute
//      actual start spread of the "concurrent" wave instead of assuming VUS=N starts perfectly
//      simultaneously.
import sse from 'k6/x/sse';
import { Trend, Counter } from 'k6/metrics';

const VUS = Number(__ENV.VUS || 5);
const PREWARM_COUNT = Number(__ENV.PREWARM_COUNT || 5);

export const options = {
    vus: VUS,
    iterations: VUS,
    maxDuration: __ENV.MAX_DURATION || '2m',
};

const GATEWAY_URL = __ENV.GATEWAY_URL || 'http://127.0.0.1:8080/chat/stream';
const CHUNK_COUNT = Number(__ENV.CHUNK_COUNT || 35);
const CHUNK_INTERVAL_MS = Number(__ENV.CHUNK_INTERVAL_MS || 200);
const FIRST_CHUNK_DELAY_MS = Number(__ENV.FIRST_CHUNK_DELAY_MS || 1000);

function requestBody() {
    return JSON.stringify({
        firstChunkDelayMs: FIRST_CHUNK_DELAY_MS,
        chunkIntervalMs: CHUNK_INTERVAL_MS,
        chunkCount: CHUNK_COUNT,
        chunkSizeBytes: 64,
    });
}

function runOneStream(onEvent) {
    return sse.open(
        GATEWAY_URL,
        { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: requestBody() },
        function (client) {
            client.on('event', onEvent);
            client.on('error', function (e) {
                console.log('sse error: ' + JSON.stringify(e));
            });
        }
    );
}

// setup() runs once, single-threaded, before any VU iteration -- prewarm calls are sequential
// (sse.open blocks until that stream's terminal signal) and never touch a wave metric.
export function setup() {
    for (let i = 0; i < PREWARM_COUNT; i++) {
        runOneStream(function () {});
    }
}

export const client_wave_start_epoch_ms = new Trend('client_wave_start_epoch_ms', true);

// actual_started -- explicit Counter, NOT inferred from a Trend's incidental .count field
// (docs/test-plan/phase4-design.md Unit 4.6 brief section 1). This is target_concurrency's
// counterpart: target=VUS (what k6 was configured to attempt), actual_started=this counter (what
// k6 actually managed to start). They can differ under real load-generator strain -- must never be
// assumed equal.
export const client_actual_started_total = new Counter('client_actual_started_total');

// Diagnostic-only trends (ALL outcomes, including rejected/failed) -- kept for observability, but
// NEVER read as the SLO-authoritative source (docs/test-plan/phase4-design.md Unit 4.6 brief
// section 3/7: rejected/failed request "duration" is near-instant and would silently drag the
// aggregate p95 down, hiding real completed-request degradation).
export const client_ttfc_seconds = new Trend('client_ttfc_seconds');
export const client_stream_duration_seconds = new Trend('client_stream_duration_seconds');

// Completed-only trends -- THE SLO-authoritative population (docs/decisions/
// phase4-scalability-definition.md section 2). Only ever .add()-ed when outcome==='completed'.
export const client_completed_ttfc_seconds = new Trend('client_completed_ttfc_seconds');
export const client_completed_stream_duration_seconds = new Trend('client_completed_stream_duration_seconds');

export const client_events_received_total = new Counter('client_events_received_total');

// Outcome-tagged (outcome: 'completed' | 'rejected' | 'failed_mid_stream' | 'failed_no_event') --
// same taxonomy as 03-constant-arrival-rate-continuous.js, so validity-vs-model-outcome logic
// (docs/test-plan/phase4-design.md Unit 4.5 brief section 11/12) can distinguish a legitimate
// M1 AbortPolicy rejection (503, MODEL_REJECTION) or a deadline timeout (failed_no_event/
// failed_mid_stream with no server-side internal_error) from an actual harness/measurement bug.
// docs/test-results/phase4/unit3-control-calibration used a simpler completed/failed binary --
// that script (01-concurrent-connections.js) is untouched; this is a separate, new file.
export const client_stream_completed_total = new Counter('client_stream_completed_total');
export const client_rejected_total = new Counter('client_rejected_total');
export const client_failed_mid_stream_total = new Counter('client_failed_mid_stream_total');
export const client_failed_no_event_total = new Counter('client_failed_no_event_total');

export default function () {
    client_wave_start_epoch_ms.add(Date.now());
    client_actual_started_total.add(1);
    const start = Date.now();
    let firstEventAt = null;
    let ttfcSeconds = null;
    let eventCount = 0;
    let sawFinal = false;

    const res = runOneStream(function (event) {
        eventCount++;
        client_events_received_total.add(1);
        if (firstEventAt === null) {
            firstEventAt = Date.now();
            ttfcSeconds = (firstEventAt - start) / 1000;
            client_ttfc_seconds.add(ttfcSeconds);
        }
        if (event.name === 'final' || (event.data && event.data.indexOf('COMPLETED') !== -1)) {
            sawFinal = true;
        }
    });

    const durationSeconds = (Date.now() - start) / 1000;

    let outcome;
    if (res && res.status === 503) {
        outcome = 'rejected';
        client_rejected_total.add(1);
    } else if (sawFinal) {
        outcome = 'completed';
        client_stream_completed_total.add(1);
    } else if (eventCount > 0) {
        outcome = 'failed_mid_stream';
        client_failed_mid_stream_total.add(1);
    } else {
        outcome = 'failed_no_event';
        client_failed_no_event_total.add(1);
    }
    client_stream_duration_seconds.add(durationSeconds, { outcome: outcome });

    // SLO-authoritative population: completed only (docs/decisions/phase4-scalability-definition.md
    // section 2). A rejected request's near-instant "duration" or a failed request's absent TTFC
    // must never enter these trends -- that would silently make SLO latency look better than it is.
    if (outcome === 'completed') {
        client_completed_stream_duration_seconds.add(durationSeconds);
        if (ttfcSeconds !== null) {
            client_completed_ttfc_seconds.add(ttfcSeconds);
        }
    }
}
