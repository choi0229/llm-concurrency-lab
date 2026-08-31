package com.llmconcurrencylab.phase4.platformqueue;

import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import org.springframework.stereotype.Component;

import com.llmconcurrencylab.phase4.common.EnvUtil;

/**
 * M1-specific metrics (docs/decisions/phase4-metrics-contract.md section 5).
 */
@Component
public class PlatformExecutorMetrics {

    // Held as a field (not a bare literal) so the gauge's weak-referenced state object isn't
    // eligible for GC — a boxed Integer outside the [-128,127] cache range would otherwise report
    // NaN once collected. Mirrors the same PT_QUEUE_CAPACITY override PlatformExecutorConfig reads
    // (test-only — Formal always uses the frozen default, 500).
    private final AtomicInteger queueCapacity = new AtomicInteger(EnvUtil.getInt("PT_QUEUE_CAPACITY", 500));

    private final Counter rejected;
    private final Timer queueWait;
    private final Timer taskDuration;

    public PlatformExecutorMetrics(MeterRegistry registry, ThreadPoolExecutor platformOutboundExecutor) {
        registry.gauge("executor.active", platformOutboundExecutor, ThreadPoolExecutor::getActiveCount);
        registry.gauge("executor.pool.size", platformOutboundExecutor, ThreadPoolExecutor::getPoolSize);
        registry.gauge("executor.queue.depth", platformOutboundExecutor, e -> e.getQueue().size());
        registry.gauge("executor.queue.capacity", queueCapacity);
        this.rejected = registry.counter("executor.rejected");
        this.queueWait = registry.timer("executor.queue.wait");
        this.taskDuration = registry.timer("executor.task.duration");
    }

    public void recordQueueWait(long nanos) {
        queueWait.record(nanos, TimeUnit.NANOSECONDS);
    }

    public void recordTaskDuration(long nanos) {
        taskDuration.record(nanos, TimeUnit.NANOSECONDS);
    }

    public void incrementRejected() {
        rejected.increment();
    }
}
