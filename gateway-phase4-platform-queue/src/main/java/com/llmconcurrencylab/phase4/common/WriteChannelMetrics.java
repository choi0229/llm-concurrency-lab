package com.llmconcurrencylab.phase4.common;

import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.atomic.AtomicInteger;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.stereotype.Component;

/**
 * Servlet write-path diagnostic metrics (docs/test-plan/phase4-design.md section 6/9, used by
 * Unit 2 section 39 postflight invariants). `servlet.write.overflow` carries a `source` tag
 * (`perstream` vs `executor`) purely for diagnostics — both map to the same `write_overflow`
 * terminal outcome (docs/test-plan/phase4-design.md section 0-2).
 */
@Component
public class WriteChannelMetrics {

    private final AtomicInteger bufferedFrames = new AtomicInteger();
    private final Counter overflowPerStream;
    private final Counter overflowExecutor;

    public WriteChannelMetrics(MeterRegistry registry, ThreadPoolExecutor writeExecutor) {
        registry.gauge("servlet.write.executor.active", writeExecutor, ThreadPoolExecutor::getActiveCount);
        registry.gauge("servlet.write.executor.pool.size", writeExecutor, ThreadPoolExecutor::getPoolSize);
        registry.gauge("servlet.write.executor.queue.depth", writeExecutor, e -> e.getQueue().size());
        registry.gauge("servlet.write.stream.buffered.frames", bufferedFrames);
        this.overflowPerStream = registry.counter("servlet.write.overflow", "source", "perstream");
        this.overflowExecutor = registry.counter("servlet.write.overflow", "source", "executor");
    }

    public void frameBuffered() {
        bufferedFrames.incrementAndGet();
    }

    public void frameDrained() {
        bufferedFrames.decrementAndGet();
    }

    public void overflowPerStream() {
        overflowPerStream.increment();
    }

    public void overflowExecutor() {
        overflowExecutor.increment();
    }

    public int bufferedFramesValue() {
        return bufferedFrames.get();
    }
}
