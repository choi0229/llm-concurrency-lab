package com.llmconcurrencylab.gatewaymvc.executor;

import java.util.concurrent.Semaphore;

import com.llmconcurrencylab.gatewaymvc.metrics.GatewayMetrics;

/**
 * Shared by THREAD_MODE=VIRTUAL_LIMITED and VIRTUAL_UNLIMITED (Unit 4). Wraps
 * a task submitted to Executors.newVirtualThreadPerTaskExecutor().
 *
 * Metric semantics (Unit 3 pre-fix 0-3 — do not confuse with request
 * success): chat_virtual_tasks_started_total/active/finished_total describe
 * the Virtual Thread task's own lifecycle. "finished" means run() returned
 * for ANY reason — success, timeout, upstream error, or client disconnect —
 * NOT gateway_request_outcome_total{outcome="completed"}, which means only
 * "the SSE stream reached natural EOF". A task can finish (this counter
 * increments) while its request's authoritative outcome is anything else.
 *
 * Permit lifecycle (Unit 3 pre-fix 0-4, VIRTUAL_LIMITED only — `semaphore` is
 * null under VIRTUAL_UNLIMITED, which has no admission gate at all, Unit 4):
 * when non-null, the admission Semaphore permit (acquired by
 * VirtualLimitedTaskSubmitter BEFORE this Runnable is even submitted) is held
 * for this task's ENTIRE run() — including the blocking upstream call and SSE
 * relay inside `delegate` — and is released here, in finally, only after
 * delegate.run() actually returns. It is deliberately NEVER released from an
 * AsyncListener callback (onTimeout/onComplete/onError): the AsyncContext can
 * be released while the underlying HttpURLConnection/readLine() call is
 * still blocking on this virtual thread (the same structural race Phase 1
 * documented in docs/test-results/phase1/absolute-deadline-blocking-read.md)
 * — releasing the permit at AsyncContext-release time would let a new
 * request start while the old, still-blocking task effectively still
 * occupies a chat task slot, pushing real admission concurrency above the
 * permit ceiling.
 */
public class VirtualTaskInstrumentedRunnable implements Runnable {

    private final Runnable delegate;
    private final GatewayMetrics metrics;
    private final Semaphore semaphore;

    /** @param semaphore null under VIRTUAL_UNLIMITED (no admission gate, Unit 4). */
    public VirtualTaskInstrumentedRunnable(Runnable delegate, GatewayMetrics metrics, Semaphore semaphore) {
        this.delegate = delegate;
        this.metrics = metrics;
        this.semaphore = semaphore;
    }

    @Override
    public void run() {
        // Smoke-test invariant (Unit 3 section 3) — VT modes must actually run
        // tasks on virtual threads. A RuntimeException here is intentional:
        // fail loudly rather than silently record metrics under the wrong
        // thread model.
        if (!Thread.currentThread().isVirtual()) {
            throw new IllegalStateException(
                    "VirtualTaskInstrumentedRunnable.run() executing on a non-virtual thread: "
                            + Thread.currentThread());
        }
        metrics.virtualTasksStartedTotal.inc();
        metrics.virtualTasksActive.inc();
        try {
            delegate.run();
        } finally {
            metrics.virtualTasksActive.dec();
            metrics.virtualTasksFinishedTotal.inc();
            if (semaphore != null) {
                semaphore.release();
            }
        }
    }
}
