package com.llmconcurrencylab.gatewaymvc.executor;

import java.util.function.DoubleConsumer;

import com.llmconcurrencylab.gatewaymvc.metrics.GatewayMetrics;

/**
 * Wraps a task submitted to the chat executor so we can time submit -> run
 * start regardless of how the task ends up running: picked up by a genuine
 * pool worker thread ("pool"), or run synchronously on the caller's own
 * thread because the queue was full and CallerRunsPolicy kicked in
 * ("caller" — see TaggingCallerRunsPolicy).
 */
public class InstrumentedRunnable implements Runnable {

    private final Runnable delegate;
    private final GatewayMetrics metrics;
    private final DoubleConsumer queueWaitSink;
    private final long submitNanos;
    private volatile boolean callerRun = false;

    /**
     * @param queueWaitSink receives the mode=pool start-delay (i.e. queue
     *     wait) in seconds, or is never called for mode=caller. Kept decoupled
     *     from any request-scoped type (executor_task_start_delay_seconds
     *     itself is still recorded unconditionally right here) so the caller
     *     can defer recording executor_queue_wait_seconds until it knows the
     *     request's final outcome (completed vs failed) — see ChatController.
     */
    public InstrumentedRunnable(Runnable delegate, GatewayMetrics metrics, DoubleConsumer queueWaitSink) {
        this.delegate = delegate;
        this.metrics = metrics;
        this.queueWaitSink = queueWaitSink;
        this.submitNanos = System.nanoTime();
    }

    /** Called by TaggingCallerRunsPolicy before invoking run() on the caller's thread. */
    public void markCallerRun() {
        this.callerRun = true;
    }

    @Override
    public void run() {
        double startDelaySeconds = (System.nanoTime() - submitNanos) / 1_000_000_000.0;
        String mode = callerRun ? "caller" : "pool";
        metrics.executorTaskStartDelaySeconds.labels(mode).observe(startDelaySeconds);
        if (!callerRun && queueWaitSink != null) {
            queueWaitSink.accept(startDelaySeconds);
        }
        metrics.executorTaskExecutionTotal.labels(mode).inc();
        delegate.run();
    }
}
