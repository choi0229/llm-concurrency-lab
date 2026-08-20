package com.llmconcurrencylab.gatewaymvc.executor;

import com.llmconcurrencylab.gatewaymvc.metrics.GatewayMetrics;

/**
 * P-E (THREAD_MODE=PLATFORM) only. Wraps a task submitted to the chat
 * ThreadPoolExecutor so executor_task_start_delay_seconds{mode="pool"} can be
 * observed at run() start — the time between submit() and the pool actually
 * starting the task (scheduling delay).
 *
 * Unlike Phase 1's InstrumentedRunnable, this has:
 *   - no mode=caller branch: P-E uses AbortPolicy, which never runs a
 *     rejected task on the caller's thread, so CallerRuns metrics don't apply
 *     to P-E (docs/test-plan/phase2-design.md section 2-1, Unit 2 pre-fix 3).
 *   - no executor_queue_wait_seconds feed: P-E's SynchronousQueue has zero
 *     queue residence by construction (a task is handed directly to a worker
 *     or the submission is rejected — it never sits in a queue), so recording
 *     a "queue wait" metric for P-E would either always observe ~0 or be
 *     misleading. executor_task_start_delay_seconds{mode="pool"} is the sole
 *     authoritative scheduling-delay metric for P-E (Unit 2 pre-fix 3) —
 *     executor_queue_wait_seconds is not declared/observed at all in this
 *     module.
 */
public class InstrumentedRunnable implements Runnable {

    private final Runnable delegate;
    private final GatewayMetrics metrics;
    private final long submitNanos;

    public InstrumentedRunnable(Runnable delegate, GatewayMetrics metrics) {
        this.delegate = delegate;
        this.metrics = metrics;
        this.submitNanos = System.nanoTime();
    }

    @Override
    public void run() {
        double startDelaySeconds = (System.nanoTime() - submitNanos) / 1_000_000_000.0;
        metrics.executorTaskStartDelaySeconds.labels("pool").observe(startDelaySeconds);
        delegate.run();
    }
}
