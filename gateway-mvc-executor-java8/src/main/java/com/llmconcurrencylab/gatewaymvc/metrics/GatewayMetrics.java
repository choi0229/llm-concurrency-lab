package com.llmconcurrencylab.gatewaymvc.metrics;

import io.prometheus.client.Counter;
import io.prometheus.client.Gauge;
import io.prometheus.client.Histogram;
import org.springframework.stereotype.Component;

/**
 * All labeled/counter/histogram metrics that don't need to read live executor
 * state. Executor-state gauges (pool size, active threads, queue size, ...)
 * are registered separately by ExecutorStateCollector once the executor bean
 * exists — see ChatExecutorConfig.
 */
@Component
public class GatewayMetrics {

    public final Gauge asyncActiveRequests = Gauge.build(
            "gateway_async_active_requests", "Servlet AsyncContext instances currently open (queued + running)")
            .register();

    public final Counter asyncTimeoutTotal = Counter.build(
            "gateway_async_timeout_total", "AsyncContext instances that hit the async timeout")
            .register();

    // Detected specifically via PrintWriter.checkError() on the write side —
    // distinct from gateway_upstream_failure_total, which covers failures
    // talking to Mock LLM. Conflating the two would hide which side actually
    // failed.
    public final Counter clientDisconnectTotal = Counter.build(
            "gateway_client_disconnect_total", "Requests where writing to the client failed (client disconnected)")
            .register();

    // Labeled by a fixed, low-cardinality reason so a formal Benchmark can
    // tell "remaining-budget clamp caused a read timeout" apart from a real
    // connect failure or a generic I/O error, instead of lumping every
    // upstream failure into one undifferentiated "FastAPI outage" bucket
    // (docs/decisions/timeout-semantics.md). Classification is by which call
    // site threw (openStream() = connect phase, getInputStream()/readLine()
    // = read phase), never by parsing exception class/message into the label
    // value itself.
    public final Counter upstreamFailureTotal = Counter.build(
            "gateway_upstream_failure_total", "Requests where the connection to Mock LLM failed or timed out")
            .labelNames("reason")
            .register();

    // Incremented once per incoming /chat/stream request, before any
    // executor submission — the denominator for the drain-time invariant
    // check (gateway_request_received_total == sum(gateway_request_outcome_total)).
    public final Counter requestReceivedTotal = Counter.build(
            "gateway_request_received_total", "Requests received at /chat/stream, before executor submission")
            .register();

    // The authoritative source of truth for Benchmark result accounting
    // (Unit 5+). Every request gets exactly one terminal outcome, recorded
    // exactly once via AsyncRequestState.releaseOnce(outcome, ...) — never
    // derived by adding up the diagnostic counters above, which are allowed
    // to overlap or fire on a code path that turns out not to be the request's
    // actual first release (see docs/decisions/request-outcome-accounting.md).
    public final Counter requestOutcomeTotal = Counter.build(
            "gateway_request_outcome_total", "Exactly-once terminal outcome per request — accounting source of truth")
            .labelNames("outcome")
            .register();

    // Deliberately separate from every other failure counter above: this one
    // fires only when a RuntimeException escapes relay() AND state.isReleased()
    // was false at that moment — i.e. it is NOT explained by the known
    // timeout/stale-task race (see docs/decisions/timeout-semantics.md). A
    // non-zero value here means investigate, it is not an expected outcome.
    public final Counter unexpectedRuntimeErrorTotal = Counter.build(
            "gateway_unexpected_runtime_error_total",
            "RuntimeExceptions during relay() not explained by the known AsyncContext timeout race")
            .register();

    // The relay loop's own per-line deadline check tripped — some chunks had
    // already been written (unlike gateway_stale_task_skipped_total, which
    // never got this far). Without this counter these outcomes were silently
    // unaccounted for: found via Unit 4-3 reconciliation (21 failed clients
    // vs only 12 covered by the other failure counters combined).
    public final Counter relayDeadlineExceededTotal = Counter.build(
            "gateway_relay_deadline_exceeded_total",
            "Requests where the absolute deadline was hit mid-relay, after at least one chunk was already sent")
            .register();

    // A task that sat in the executor queue long enough for its AsyncContext
    // to already hit CHAT_TOTAL_TIMEOUT_MS (via onTimeout) before a worker
    // thread ever got to it. Distinct from executor_rejected: this task WAS
    // admitted and DID consume a worker slot, it just did no useful work —
    // quantifies capacity wasted purely by excessive queue wait.
    public final Counter staleTaskSkippedTotal = Counter.build(
            "gateway_stale_task_skipped_total",
            "Queued tasks whose AsyncContext had already timed out before a worker thread started them")
            .register();

    public final Counter executorRejectedTotal = Counter.build(
            "gateway_executor_rejected_total", "Tasks rejected by the chat executor (AbortPolicy)")
            .register();

    // Only ever observed for tasks that actually ran via a pool worker thread
    // (mode=pool) — see InstrumentedRunnable. CallerRunsPolicy tasks never sit
    // in the queue, so they are intentionally excluded from this metric.
    //
    // Labeled by outcome=completed|failed and observed only once the outcome
    // is known (recorded in ChatController.relay()'s finally, not at task
    // start) — a request that eventually timed out spent just as long queued
    // as one that succeeded, but averaging them together hides survivorship
    // bias: at high concurrency the "failed" population's queue wait is
    // systematically the *longest* one (that's *why* it failed), so a single
    // blended average understates how bad queueing gets for the tail.
    public final Histogram executorQueueWaitSeconds = Histogram.build(
            "executor_queue_wait_seconds", "Time a task actually spent in the executor queue before a worker picked it up")
            .buckets(FINE_LATENCY_BUCKETS)
            .labelNames("outcome")
            .register();

    // General concept: submit() -> run() start, for ALL tasks regardless of how
    // they started running. mode=pool tasks show real queueing/scheduling delay;
    // mode=caller tasks (CallerRunsPolicy) are near-zero by construction, since
    // the caller thread runs them immediately inline — the cost there shows up
    // as Tomcat thread occupancy, not as start delay.
    public final Histogram executorTaskStartDelaySeconds = Histogram.build(
            "executor_task_start_delay_seconds", "Time from submit() to the task's run() actually starting")
            .buckets(FINE_LATENCY_BUCKETS)
            .labelNames("mode")
            .register();

    public final Counter executorTaskExecutionTotal = Counter.build(
            "executor_task_execution_total", "Tasks that started running, by execution mode")
            .labelNames("mode")
            .register();

    public final Histogram ttfbSeconds = Histogram.build(
            "gateway_ttfb_seconds", "Time from request received to first chunk relayed to the client")
            .buckets(FINE_LATENCY_BUCKETS)
            .register();

    // Unit 6 calibration (2026-08-13, docs/test-results/phase1/unit6-metric-calibration.md):
    // the original coarse tail (..., 10, 30, 60) made Prometheus's classic-histogram
    // histogram_quantile() linearly interpolate across a 10s-60s span for exactly the
    // latency range this Baseline's results live in (queue wait / TTFB / deadline-adjacent
    // outcomes), producing p95/p99 estimates far from the true value — confirmed by
    // comparing against k6's own (non-bucketed) client_ttfc_seconds Trend, which does not
    // have this problem. Widening the bucket count in the 1s-60s range (matching
    // CHAT_TOTAL_TIMEOUT_MS) trades a bit of scrape-payload size for materially better
    // p95/p99 accuracy on server-side histograms. Every Histogram in this class must keep
    // using the same boundaries (docs/decisions/monitoring-baseline.md) for cross-run/
    // cross-implementation quantile comparability.
    private static final double[] FINE_LATENCY_BUCKETS = {
            0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5,
            1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 15, 18, 21, 24, 27, 30, 35, 40, 45, 50, 55, 60,
    };
}
