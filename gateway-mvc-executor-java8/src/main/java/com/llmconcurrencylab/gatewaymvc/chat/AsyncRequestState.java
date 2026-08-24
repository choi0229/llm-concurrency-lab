package com.llmconcurrencylab.gatewaymvc.chat;

import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Per-request idempotency guard. AsyncListener.onComplete/onTimeout/onError
 * can each fire for the same request (e.g. onTimeout calling
 * asyncContext.complete() also triggers onComplete afterwards), so without
 * this guard the async_active_requests gauge would be decremented more than
 * once for a single request.
 */
public class AsyncRequestState {

    private final AtomicBoolean released = new AtomicBoolean(false);
    private volatile String outcome;

    /**
     * @param outcome the request's terminal outcome (one of the fixed
     *     gateway_request_outcome_total label values). Recorded only if this
     *     call wins the race — callers are expected to increment
     *     gateway_request_outcome_total{outcome} for this value from inside
     *     releaseAction, so the metric only ever moves on the winning call.
     * @return true if this call was the one that performed the transition.
     */
    public boolean releaseOnce(String outcome, Runnable releaseAction) {
        boolean wasFirst = released.compareAndSet(false, true);
        if (wasFirst) {
            this.outcome = outcome;
            releaseAction.run();
        }
        return wasFirst;
    }

    /**
     * True once the AsyncContext has already been finalized by a listener
     * callback (typically onTimeout firing while this task was still sitting
     * in the executor queue). The worker task must check this before touching
     * the response — otherwise it throws IllegalStateException("The request
     * associated with the AsyncContext has already completed processing"),
     * which kills the pool worker thread that hits it.
     */
    public boolean isReleased() {
        return released.get();
    }

    /** The winning outcome, or null if the request hasn't been released yet. */
    public String getOutcome() {
        return outcome;
    }

    // Set once by InstrumentedRunnable (mode=pool only) at task-start, read
    // once relay() knows the request's final outcome — see
    // GatewayMetrics.executorQueueWaitSeconds for why this is deferred
    // instead of observed immediately.
    private volatile Double queueWaitSeconds;

    public void setQueueWaitSeconds(double seconds) {
        this.queueWaitSeconds = seconds;
    }

    /** Null for mode=caller tasks, which never sat in the queue. */
    public Double getQueueWaitSeconds() {
        return queueWaitSeconds;
    }
}
