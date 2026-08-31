package com.llmconcurrencylab.phase4.virtualthread;

import java.util.concurrent.atomic.AtomicInteger;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.stereotype.Component;

/**
 * M2-specific metrics (docs/decisions/phase4-metrics-contract.md section 6). Deliberately kept
 * separate from the common JVM live-platform-thread metric — virtual task count and platform
 * thread count are never conflated (H4-c is exactly the question of whether they move 1:1).
 */
@Component
public class VirtualTaskMetrics {

    private final AtomicInteger active = new AtomicInteger();
    private final Counter started;

    public VirtualTaskMetrics(MeterRegistry registry) {
        registry.gauge("virtual.tasks.active", active);
        this.started = registry.counter("virtual.tasks.started");
    }

    public void taskStarted() {
        started.increment();
        active.incrementAndGet();
    }

    public void taskEnded() {
        active.decrementAndGet();
    }

    public int activeValue() {
        return active.get();
    }
}
