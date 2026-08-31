package com.llmconcurrencylab.phase4.platformqueue;

import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ThreadPoolExecutor;

import com.llmconcurrencylab.phase4.common.ChatTaskSubmitter;
import com.llmconcurrencylab.phase4.common.RequestLifecycle;
import org.springframework.stereotype.Component;

/**
 * M1's {@link ChatTaskSubmitter}: submits to the bounded {@link PlatformExecutorConfig} executor.
 * Registers a cancel hook so a request that times out / disconnects while still queued is removed
 * from the queue (docs/test-plan/phase4-design.md section 11, Unit 2 section 11 — no phantom
 * upstream call for a task that never gets a chance to see
 * {@code RequestLifecycle.isTerminal()==true} because it never runs at all).
 */
@Component
public class PlatformTaskSubmitter implements ChatTaskSubmitter {

    private final ThreadPoolExecutor executor;
    private final PlatformExecutorMetrics metrics;

    public PlatformTaskSubmitter(ThreadPoolExecutor platformOutboundExecutor, PlatformExecutorMetrics metrics) {
        this.executor = platformOutboundExecutor;
        this.metrics = metrics;
    }

    @Override
    public boolean trySubmit(RequestLifecycle lifecycle, Runnable task) {
        long submitNanos = System.nanoTime();
        Runnable timed = () -> {
            metrics.recordQueueWait(System.nanoTime() - submitNanos);
            long taskStartNanos = System.nanoTime();
            try {
                task.run();
            } finally {
                metrics.recordTaskDuration(System.nanoTime() - taskStartNanos);
            }
        };
        try {
            executor.execute(timed);
            lifecycle.setCancelHook(() -> executor.remove(timed));
            return true;
        } catch (RejectedExecutionException e) {
            metrics.incrementRejected();
            return false;
        }
    }
}
