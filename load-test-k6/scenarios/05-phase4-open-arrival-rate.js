// Scenario 5 — Phase 4 Open-model Constant Arrival Rate (Screening + Formal).
//
// Forked from 03-constant-arrival-rate-continuous.js (that file is explicitly "Phase 2 Formal
// only" per its own header and is left untouched -- same fork-don't-modify precedent that file
// itself used when it forked from 02-constant-arrival-rate.js). Reuses that file's continuous
// warm-up->measurement design (single k6 process, phase boundary read once via setup(), no drain
// between warm-up and measurement) verbatim.
//
// The one addition: `client_completed_stream_duration_seconds`, a completed-only stream-duration
// Trend mirroring `client_ttfc_completed_seconds` (which 03 already has) and matching
// 04-phase4-closed-wave.js's existing completed-only pattern for TTFC/duration pairs. Without it,
// docs/decisions/phase4-scalability-definition.md section 2-1's frozen "SLO population is
// completed-only" requirement (stream duration p95 <= 10.0s) cannot be computed from k6's own
// summary export -- `client_stream_duration_seconds` mixes all outcomes, and rejected/failed
// "durations" are near-instant, which would silently drag the aggregate down exactly as that
// section warns against.
//
// Phase 4 Open Unit 7.5 client-lifecycle fix (docs/test-results/phase4/
// unit7.5-client-lifecycle-fix/SUMMARY.md): xk6-sse@v0.1.11 constructs a brand-new http.Transport
// per sse.open() call and never reuses or explicitly closes it (confirmed via source read, Unit
// 7.4) -- its own `closeResponseBody()` (auto-invoked on stream EOF) closes only the response body,
// not the transport's idle-connection pool; that cleanup exists solely in `Client.close()`, which
// nothing in this project's workload scripts ever called. Every completed request therefore left
// its idle, un-reused per-iteration Transport connection open and unclosed on the client side --
// only the *server's* keepAliveTimeout eventually reclaimed it -- multiplying the effective
// concurrent-connection population far past nominal expectations (Unit
// 7.4: ~8.25 connections per peak-concurrent VU). Fix: capture the `client` reference from the
// setup callback and call `.close()` immediately after `sse.open()` returns. This is safe because
// `sse.open()` is documented (and confirmed by source read) to block until the stream already
// reached a terminal state (EOF/ctx-done) or never got a client at all (a connection that failed
// before the callback ran) -- so the call can never truncate an in-progress stream, and it runs
// through a single, unconditional call site (never from inside multiple event callbacks), so it is
// exactly-once by construction. `Client.close()`'s three internal steps
// (`closeResponseBody`/`cancelRequest`/`CloseIdleConnections`) are each independently idempotent
// (sync.Once-guarded, a no-op CancelFunc reinvocation, and a no-op close of an already-empty idle
// pool respectively -- confirmed via source read), so calling it after xk6-sse's own automatic
// cleanup already ran is harmless. No SSE/workload/timing semantics change.
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
// New in this fork -- see file header.
export const client_completed_stream_duration_seconds = new Trend('client_completed_stream_duration_seconds');

export const client_completed_total = new Counter('client_completed_total');
export const client_rejected_total = new Counter('client_rejected_total');
export const client_failed_mid_stream_total = new Counter('client_failed_mid_stream_total');
export const client_failed_no_event_total = new Counter('client_failed_no_event_total');
export const client_events_received_total = new Counter('client_events_received_total');
export const measurement_iterations_started_total = new Counter('measurement_iterations_started_total');

const GATEWAY_URL = __ENV.GATEWAY_URL || 'http://gateway:8080/chat/stream';
const CHUNK_COUNT = Number(__ENV.CHUNK_COUNT || 35);
const CHUNK_INTERVAL_MS = Number(__ENV.CHUNK_INTERVAL_MS || 200);
const FIRST_CHUNK_DELAY_MS = Number(__ENV.FIRST_CHUNK_DELAY_MS || 1000);

export default function (data) {
    const start = Date.now();
    const phase = (data && start >= data.measurementStartMs) ? 'measurement' : 'warmup';
    if (phase === 'measurement') {
        measurement_iterations_started_total.add(1);
    }

    let firstEventAt = null;
    let eventCount = 0;
    let sawFinal = false;
    let sseClientRef = null;

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
            sseClientRef = client;
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

    // sse.open() only returns once the stream has already reached a terminal state (or never
    // acquired a client at all, if the connection itself failed before the setup callback ran --
    // see the Unit 7.5 header note above) -- so this is always a post-terminal, exactly-once cleanup
    // call, never a premature one.
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
