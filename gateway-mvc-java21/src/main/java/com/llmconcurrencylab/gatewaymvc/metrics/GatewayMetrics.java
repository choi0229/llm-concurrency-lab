package com.llmconcurrencylab.gatewaymvc.metrics;

import io.prometheus.client.Counter;
import io.prometheus.client.Gauge;
import io.prometheus.client.Histogram;
import org.springframework.stereotype.Component;

/**
 * Common request/outcome accounting metrics reused byte-for-byte (same
 * names/labels) from Phase 1's GatewayMetrics — see docs/test-plan/
 * phase2-design.md section 8 ("공통 request/outcome instrumentation을
 * 재사용하고 executor-specific metric은 분리한다") and section 10 (final
 * histogram bucket schema applied from Day 1) — plus P-E's executor-specific
 * scheduling-delay metric (Unit 2).
 *
 * Deliberately NOT declared (Unit 2 pre-fix 3, docs/test-plan/
 * phase2-design.md section 2-1): executor_queue_wait_seconds. P-E's
 * SynchronousQueue has zero queue residence by construction — a task is
 * handed directly to a worker or the submission is rejected, it never sits
 * in a queue — so this metric would either always be ~0 or be misleading if
 * mis-read as "queue residence time". executor_task_start_delay_seconds{
 * mode="pool"} is the sole authoritative scheduling-delay metric for P-E.
 *
 * Virtual task metrics (Unit 3, VT-Limited only): chat_virtual_tasks_active/
 * started_total/finished_total describe the Virtual Thread task's own
 * lifecycle (started = run() entered, finished = run() returned for ANY
 * reason). They do NOT mean the same thing as
 * gateway_request_outcome_total{outcome="completed"} — "finished" ≠
 * "succeeded". See VirtualTaskInstrumentedRunnable's Javadoc (Unit 3
 * pre-fix 0-3) for the full explanation; do not confuse the two when reading
 * dashboards.
 */
@Component
public class GatewayMetrics {

    public final Gauge asyncActiveRequests = Gauge.build(
            "gateway_async_active_requests", "Servlet AsyncContext instances currently open (queued + running)")
            .register();

    public final Counter asyncTimeoutTotal = Counter.build(
            "gateway_async_timeout_total", "AsyncContext instances that hit the async timeout")
            .register();

    public final Counter clientDisconnectTotal = Counter.build(
            "gateway_client_disconnect_total", "Requests where writing to the client failed (client disconnected)")
            .register();

    public final Counter upstreamFailureTotal = Counter.build(
            "gateway_upstream_failure_total", "Requests where the connection to Mock LLM failed or timed out")
            .labelNames("reason")
            .register();

    public final Counter requestReceivedTotal = Counter.build(
            "gateway_request_received_total", "Requests received at /chat/stream, before executor submission")
            .register();

    // The authoritative source of truth for Benchmark result accounting —
    // see docs/decisions/request-outcome-accounting.md. Unchanged from Phase 1.
    public final Counter requestOutcomeTotal = Counter.build(
            "gateway_request_outcome_total", "Exactly-once terminal outcome per request — accounting source of truth")
            .labelNames("outcome")
            .register();

    public final Counter unexpectedRuntimeErrorTotal = Counter.build(
            "gateway_unexpected_runtime_error_total",
            "RuntimeExceptions during relay() not explained by the known AsyncContext timeout race")
            .register();

    public final Counter relayDeadlineExceededTotal = Counter.build(
            "gateway_relay_deadline_exceeded_total",
            "Requests where the absolute deadline was hit mid-relay, after at least one chunk was already sent")
            .register();

    public final Counter staleTaskSkippedTotal = Counter.build(
            "gateway_stale_task_skipped_total",
            "Queued tasks whose AsyncContext had already timed out before a worker thread started them")
            .register();

    public final Counter executorRejectedTotal = Counter.build(
            "gateway_executor_rejected_total", "Tasks rejected by the chat executor")
            .register();

    // P-E only (mode="pool" always, no mode="caller" — see class Javadoc).
    // submit() -> run() start; the authoritative scheduling-delay metric for
    // P-E (Unit 2 pre-fix 3). The "mode" label is kept for schema parity with
    // Phase 1's metric definition even though P-E never populates
    // mode="caller" (AbortPolicy has no caller-runs path).
    public final Histogram executorTaskStartDelaySeconds = Histogram.build(
            "executor_task_start_delay_seconds", "Time from submit() to the task's run() actually starting")
            .buckets(FINE_LATENCY_BUCKETS)
            .labelNames("mode")
            .register();

    // VT-Limited only (Unit 3). Task lifecycle counters — NOT request-success
    // counters. See VirtualTaskInstrumentedRunnable's Javadoc for the exact
    // semantics of "started"/"finished" (Unit 3 pre-fix 0-3).
    //
    // Unit 3.5 clarification: "active" means the task has ENTERED
    // VirtualTaskInstrumentedRunnable.run() and not yet returned from it —
    // NOT "submitted". The admission Semaphore permit is acquired BEFORE
    // submission (in VirtualLimitedTaskSubmitter, before the virtual thread
    // is even created), so there is a brief window between permit
    // acquisition and this gauge incrementing where permits-in-use can
    // momentarily exceed chat_virtual_tasks_active. This gauge is NOT the
    // admission-ceiling source of truth — the Semaphore itself is (see
    // VirtualLimitedTaskSubmitter.availablePermits(), diagnostic-only).
    public final Gauge virtualTasksActive = Gauge.build(
            "chat_virtual_tasks_active",
            "Virtual thread chat tasks that have entered run() and not yet returned "
                    + "(not the admission-ceiling source of truth — see Semaphore permits)")
            .register();

    public final Counter virtualTasksStartedTotal = Counter.build(
            "chat_virtual_tasks_started_total", "Virtual thread chat tasks whose run() has started")
            .register();

    public final Counter virtualTasksFinishedTotal = Counter.build(
            "chat_virtual_tasks_finished_total",
            "Virtual thread chat tasks whose run() has returned, for ANY reason (success, timeout, error, or "
                    + "client disconnect) — NOT the same as gateway_request_outcome_total{outcome=\"completed\"}")
            .register();

    // VT-only diagnostic — never the accounting source of truth for admission
    // rejection across modes. gateway_request_outcome_total{outcome=
    // "rejected"} is that source of truth for both P-E and VT-Limited (Unit 3
    // section 2).
    public final Counter virtualAdmissionRejectedTotal = Counter.build(
            "chat_virtual_admission_rejected_total",
            "VT-Limited admission-Semaphore tryAcquire() failures (diagnostic only, not the cross-mode "
                    + "rejected-outcome source of truth)")
            .register();

    // Client-side k6 Trend counterpart: client_ttfc_completed_seconds /
    // client_ttfc_seconds (see docs/test-plan/phase2-design.md section 8-1,
    // "client-side metric과 대응 가능한 server metric").
    public final Histogram ttfbSeconds = Histogram.build(
            "gateway_ttfb_seconds", "Time from request received to first chunk relayed to the client")
            .buckets(FINE_LATENCY_BUCKETS)
            .register();

    // Phase 2 Day 1 schema — the FINAL bucket schema frozen in Unit 6.7
    // (docs/decisions/monitoring-baseline.md section 6), applied from the
    // start rather than being recalibrated mid-campaign like Phase 1 was.
    // Every implementation in Phase 2 (P-E/VT-Limited/VT-Unlimited) must keep
    // using these exact boundaries for cross-run comparability within Phase 2
    // (docs/test-plan/phase2-design.md section 10). NOT bit-for-bit identical
    // to Phase 1's bucket schema — see that section's Cross-Phase Metric
    // Compatibility policy for why Phase 1 vs Phase 2 server histograms are
    // not compared as equal-precision absolute values.
    private static final double[] FINE_LATENCY_BUCKETS = {
            0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5,
            0.75, 0.9, 1.0, 1.05, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5,
            3, 4, 5, 6, 7, 8, 10, 12, 15, 18, 21, 24, 27, 30, 35, 40, 45, 50, 55, 60,
    };
}
