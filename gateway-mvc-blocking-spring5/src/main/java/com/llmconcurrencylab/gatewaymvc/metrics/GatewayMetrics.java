package com.llmconcurrencylab.gatewaymvc.metrics;

import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import org.springframework.stereotype.Component;

/**
 * Phase 3 common + P3-A-specific metrics on a single Micrometer {@link MeterRegistry}
 * (docs/decisions/phase3-metrics-contract.md §1 — Micrometer-only, not Phase 1/2's
 * io.prometheus:simpleclient). Names/semantics follow that ADR's §3 (common) and §4-1/§4-2
 * (servlet write / P3-A blocking outbound) tables.
 *
 * gateway.write.overflow is used for both the "common" and "P3-A/B servlet write" entries in
 * the ADR — they describe the same event from two angles, so this implementation keeps a single
 * counter rather than two metrics with identical semantics (documented in the module README).
 */
@Component
public class GatewayMetrics {

    private final MeterRegistry registry;

    public final Counter requestsStarted;
    public final Counter admissionRejected;
    public final Counter clientDisconnect;
    public final Counter timeoutTotal;
    public final Counter writeOverflow;
    public final Counter watchdogActivated;
    public final Counter bytesRelayed;
    public final Counter blockingExecutorRejected;
    public final Counter servletWriteExecutorRejected;

    public final Timer firstChunkRelay;
    public final Timer streamDuration;
    public final Timer writeDuration;
    public final Timer writeQueueWait;
    public final Timer blockingTaskDuration;

    private final AtomicInteger activeStreams = new AtomicInteger(0);
    private final AtomicInteger admissionActive = new AtomicInteger(0);
    private final AtomicInteger upstreamActive = new AtomicInteger(0);
    private final AtomicInteger streamBufferedFrames = new AtomicInteger(0);

    public GatewayMetrics(MeterRegistry registry) {
        this.registry = registry;

        this.requestsStarted = registry.counter("gateway.requests.started");
        this.admissionRejected = registry.counter("gateway.admission.rejected");
        this.clientDisconnect = registry.counter("gateway.client.disconnect");
        this.timeoutTotal = registry.counter("gateway.timeout");
        this.writeOverflow = registry.counter("gateway.write.overflow");
        this.watchdogActivated = registry.counter("gateway.watchdog.activated");
        this.bytesRelayed = registry.counter("gateway.bytes.relayed");
        this.blockingExecutorRejected = registry.counter("outbound.blocking.executor.rejected");
        this.servletWriteExecutorRejected = registry.counter("servlet.write.executor.rejected");

        this.firstChunkRelay = registry.timer("gateway.first.chunk.relay");
        this.streamDuration = registry.timer("gateway.stream.duration");
        this.writeDuration = registry.timer("servlet.write.duration");
        this.writeQueueWait = registry.timer("servlet.write.queue.wait");
        this.blockingTaskDuration = registry.timer("outbound.blocking.task.duration");

        Gauge.builder("gateway.active.streams", activeStreams, AtomicInteger::get).register(registry);
        Gauge.builder("gateway.admission.active", admissionActive, AtomicInteger::get).register(registry);
        Gauge.builder("gateway.upstream.active", upstreamActive, AtomicInteger::get).register(registry);
        Gauge.builder("servlet.write.stream.buffered.frames", streamBufferedFrames, AtomicInteger::get)
                .register(registry);
    }

    /** One Counter per outcome value (phase3-design.md §5's fixed 7-value set) — authoritative accounting. */
    public void recordOutcome(String outcome) {
        registry.counter("gateway.requests", "outcome", outcome).increment();
    }

    /** Diagnostic — Gateway cancelled/gave up on the upstream call (any terminal path that isn't a clean EOF). */
    public void recordUpstreamCancel() {
        registry.counter("gateway.upstream.cancel").increment();
    }

    public void activeStreamsIncrement() {
        activeStreams.incrementAndGet();
    }

    public void activeStreamsDecrement() {
        activeStreams.decrementAndGet();
    }

    public void admissionActiveIncrement() {
        admissionActive.incrementAndGet();
    }

    public void admissionActiveDecrement() {
        admissionActive.decrementAndGet();
    }

    public void upstreamActiveIncrement() {
        upstreamActive.incrementAndGet();
    }

    public void upstreamActiveDecrement() {
        upstreamActive.decrementAndGet();
    }

    public void streamBufferedFramesIncrement() {
        streamBufferedFrames.incrementAndGet();
    }

    public void streamBufferedFramesDecrement() {
        streamBufferedFrames.decrementAndGet();
    }

    /** Diagnostic-only accessors for postflight invariant checks (Unit 2 §24 / ADR §26). */
    public int activeStreamsValue() {
        return activeStreams.get();
    }

    public int admissionActiveValue() {
        return admissionActive.get();
    }

    public int upstreamActiveValue() {
        return upstreamActive.get();
    }

    public int streamBufferedFramesValue() {
        return streamBufferedFrames.get();
    }

    /** Registers the outbound.blocking.executor.{active,pool.size,largest.pool.size,queue.depth} gauges. */
    public void bindBlockingOutboundExecutor(ThreadPoolExecutor executor) {
        Gauge.builder("outbound.blocking.executor.active", executor, ThreadPoolExecutor::getActiveCount)
                .register(registry);
        Gauge.builder("outbound.blocking.executor.pool.size", executor, ThreadPoolExecutor::getPoolSize)
                .register(registry);
        Gauge.builder("outbound.blocking.executor.largest.pool.size", executor, ThreadPoolExecutor::getLargestPoolSize)
                .register(registry);
        Gauge.builder("outbound.blocking.executor.queue.depth", executor, e -> e.getQueue().size())
                .register(registry);
    }

    /** Registers the servlet.write.executor.{active,pool.size,queue.depth} gauges. */
    public void bindServletWriteExecutor(ThreadPoolExecutor executor) {
        Gauge.builder("servlet.write.executor.active", executor, ThreadPoolExecutor::getActiveCount)
                .register(registry);
        Gauge.builder("servlet.write.executor.pool.size", executor, ThreadPoolExecutor::getPoolSize)
                .register(registry);
        Gauge.builder("servlet.write.executor.queue.depth", executor, e -> e.getQueue().size())
                .register(registry);
    }

    public void recordWriteDuration(long nanos) {
        writeDuration.record(nanos, TimeUnit.NANOSECONDS);
    }

    public void recordWriteQueueWait(long nanos) {
        writeQueueWait.record(nanos, TimeUnit.NANOSECONDS);
    }

    public void recordFirstChunkRelay(long nanos) {
        firstChunkRelay.record(nanos, TimeUnit.NANOSECONDS);
    }

    public void recordStreamDuration(long nanos) {
        streamDuration.record(nanos, TimeUnit.NANOSECONDS);
    }

    public void recordBlockingTaskDuration(long nanos) {
        blockingTaskDuration.record(nanos, TimeUnit.NANOSECONDS);
    }
}
