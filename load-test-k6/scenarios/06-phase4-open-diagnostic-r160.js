// Scenario 6 — Phase 4 Open Unit 7.2 R=160 Transport/Front-door Isolation DIAGNOSTIC ONLY.
//
// Forked from 05-phase4-open-arrival-rate.js (frozen after first Open Gateway load per
// docs/test-plan/phase4-open-screening-protocol.md section 4 -- not modified, forked instead, same
// precedent that file itself documents). Workload semantics (SSE request shape, constant-arrival-rate
// executor config, warmup/measurement phase split, SLO population rules) are IDENTICAL to 05 --
// nothing about the offered load or timing changes. The only additions are extra client-side
// observability counters/logging to localize where the client-started-vs-Gateway-dispatched
// population gap documented in docs/test-results/phase4/unit7.1-m2-r160-frontdoor-forensics/
// occurs. This scenario produces DIAGNOSTIC artifacts only -- never a canonical Screening/Formal
// point (see docs/test-results/phase4/unit7.2-r160-frontdoor-isolation/ harness wiring).
//
// New counters (all in addition to 05's existing ones, none of 05's are removed or renamed):
//   client_iterations_started_total  -- every iteration, before sse.open() is even called.
//   client_sse_open_attempt_total    -- immediately before calling sse.open().
//   client_sse_open_success_total    -- incremented inside client.on('open', ...), i.e. xk6-sse's
//                                        own signal (if it fires) that a session was established,
//                                        distinct from "first SSE event received".
//   client_first_event_total         -- first client.on('event', ...) firing (mirrors the existing
//                                        TTFC trend's own condition, exposed as an explicit counter).
//   client_sse_error_total           -- client.on('error', ...) firing, any count.
//   client_zero_event_terminal_total -- same condition as 05's existing client_failed_no_event_total,
//                                        added under this Unit's requested name too (identical value,
//                                        kept separate rather than renaming 05's counter).
//
// Error/category limitation (disclosed, not worked around): xk6-sse v0.1.11's error callback has
// been observed, across every prior capture in this project (Unit 4-4's sse-verification-test.js
// runs and Unit 1's 01-concurrent-connections.js runs), to pass an EMPTY object -- `JSON.stringify(e)
// === "{}"` every time. This script logs whatever the callback actually provides (never fabricated),
// which is expected to again be empty based on that history; no invented error-code taxonomy
// (connect/dial/connection_reset/etc.) is applied unless the raw object actually contains such a
// field this time.
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
            gracefulStop: __ENV.GRACEFUL_STOP || '90s',
        },
    },
};

export const measurement_start_epoch_s = new Gauge('measurement_start_epoch_s');

export function setup() {
    const measurementStartMs = Date.now() + WARMUP_SEC * 1000;
    measurement_start_epoch_s.add(measurementStartMs / 1000);
    return { measurementStartMs };
}

export const client_ttfc_seconds = new Trend('client_ttfc_seconds');
export const client_stream_duration_seconds = new Trend('client_stream_duration_seconds');
export const client_ttfc_completed_seconds = new Trend('client_ttfc_completed_seconds');
export const client_completed_stream_duration_seconds = new Trend('client_completed_stream_duration_seconds');

export const client_completed_total = new Counter('client_completed_total');
export const client_rejected_total = new Counter('client_rejected_total');
export const client_failed_mid_stream_total = new Counter('client_failed_mid_stream_total');
export const client_failed_no_event_total = new Counter('client_failed_no_event_total');
export const client_events_received_total = new Counter('client_events_received_total');
export const measurement_iterations_started_total = new Counter('measurement_iterations_started_total');

// New in this diagnostic fork -- see file header.
export const client_iterations_started_total = new Counter('client_iterations_started_total');
export const client_sse_open_attempt_total = new Counter('client_sse_open_attempt_total');
export const client_sse_open_success_total = new Counter('client_sse_open_success_total');
export const client_first_event_total = new Counter('client_first_event_total');
export const client_sse_error_total = new Counter('client_sse_error_total');
export const client_zero_event_terminal_total = new Counter('client_zero_event_terminal_total');

const GATEWAY_URL = __ENV.GATEWAY_URL || 'http://gateway:8080/chat/stream';
const CHUNK_COUNT = Number(__ENV.CHUNK_COUNT || 35);
const CHUNK_INTERVAL_MS = Number(__ENV.CHUNK_INTERVAL_MS || 200);
const FIRST_CHUNK_DELAY_MS = Number(__ENV.FIRST_CHUNK_DELAY_MS || 1000);

export default function (data) {
    const start = Date.now();
    const phase = (data && start >= data.measurementStartMs) ? 'measurement' : 'warmup';
    client_iterations_started_total.add(1);
    if (phase === 'measurement') {
        measurement_iterations_started_total.add(1);
    }

    let firstEventAt = null;
    let eventCount = 0;
    let sawFinal = false;
    let sawOpen = false;
    let sawError = false;
    let sseClientRef = null;

    const body = JSON.stringify({
        firstChunkDelayMs: FIRST_CHUNK_DELAY_MS,
        chunkIntervalMs: CHUNK_INTERVAL_MS,
        chunkCount: CHUNK_COUNT,
        chunkSizeBytes: 64,
    });

    client_sse_open_attempt_total.add(1);
    const res = sse.open(
        GATEWAY_URL,
        { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body },
        function (client) {
            sseClientRef = client;
            client.on('open', function () {
                sawOpen = true;
                client_sse_open_success_total.add(1);
            });
            client.on('event', function (event) {
                eventCount++;
                if (phase === 'measurement') {
                    client_events_received_total.add(1);
                }
                if (firstEventAt === null) {
                    firstEventAt = Date.now();
                    client_first_event_total.add(1);
                }
                if (event.name === 'final' || (event.data && event.data.indexOf('COMPLETED') !== -1)) {
                    sawFinal = true;
                }
            });
            client.on('error', function (e) {
                sawError = true;
                client_sse_error_total.add(1);
                // Diagnostic-only log, never a metric label (section 6 of the governing
                // instruction) -- captures exactly what xk6-sse provides, no fabricated fields.
                console.log('diag sse_error phase=' + phase + ' t=' + Date.now() +
                    ' elapsed_ms=' + (Date.now() - start) + ' raw=' + JSON.stringify(e));
            });
        }
    );

    // Unit 7.5 client-lifecycle fix, same rationale/safety as 05-phase4-open-arrival-rate.js's
    // header note -- post-terminal, exactly-once, harmless if xk6-sse already auto-cleaned up.
    if (sseClientRef !== null) {
        try {
            sseClientRef.close();
        } catch (e) {
            console.log('sse client.close() cleanup error: ' + JSON.stringify(e));
        }
    }

    const durationSeconds = (Date.now() - start) / 1000;

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
        client_zero_event_terminal_total.add(1);
        // Diagnostic-only: for the exact population this Unit exists to explain, log the res
        // object's shape and the open/error flags observed, unconditionally (not sampled) --
        // this is the one piece of new evidence this fork exists to gather.
        console.log('diag zero_event_terminal phase=' + phase + ' t=' + Date.now() +
            ' elapsed_ms=' + Math.round(durationSeconds * 1000) +
            ' sawOpen=' + sawOpen + ' sawError=' + sawError + ' eventCount=' + eventCount +
            ' res_present=' + (res !== null && res !== undefined) +
            ' res_status=' + (res ? res.status : 'n/a') +
            ' res_json=' + (function () { try { return JSON.stringify(res); } catch (e) { return 'unstringifiable'; } })());
    }

    if (firstEventAt !== null) {
        const ttfc = (firstEventAt - start) / 1000;
        client_ttfc_seconds.add(ttfc, { outcome: outcome });
        if (outcome === 'completed') {
            client_ttfc_completed_seconds.add(ttfc);
        }
    }
    client_stream_duration_seconds.add(durationSeconds, { outcome: outcome });
    if (outcome === 'completed') {
        client_completed_stream_duration_seconds.add(durationSeconds);
    }
}
