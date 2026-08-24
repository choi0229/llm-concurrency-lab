package com.llmconcurrencylab.gatewaymvc.metrics;

import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import org.springframework.stereotype.Component;

/**
 * Phase 3 common metrics for P3-C, ported from P3-A/B's GatewayMetrics with everything
 * Servlet/blocking-executor-specific removed entirely (Unit 4 §23 — "없는 metric을 0 Gauge로
 * 꾸미지 않는다"): no {@code outbound.blocking.*} (no blocking outbound executor here, same as
 * P3-B), and — new for P3-C — no {@code servlet.write.*} at all (no Servlet write executor, no
 * per-stream write buffer, no {@code write_overflow} outcome is structurally reachable — Unit 4
 * §10). The absence of these metrics is itself part of Experiment B's result, not an oversight.
 */
@Component
public class GatewayMetrics {

    private final MeterRegistry registry;

    public final Counter requestsStarted;
    public final Counter admissionRejected;
    public final Counter clientDisconnect;
    public final Counter timeoutTotal;
    public final Counter bytesRelayed;

    public final Timer firstChunkRelay;
    public final Timer streamDuration;

    private final AtomicInteger activeStreams = new AtomicInteger(0);
    private final AtomicInteger admissionActive = new AtomicInteger(0);
    private final AtomicInteger upstreamActive = new AtomicInteger(0);

    public GatewayMetrics(MeterRegistry registry) {
        this.registry = registry;

        this.requestsStarted = registry.counter("gateway.requests.started");
        this.admissionRejected = registry.counter("gateway.admission.rejected");
        this.clientDisconnect = registry.counter("gateway.client.disconnect");
        this.timeoutTotal = registry.counter("gateway.timeout");
        this.bytesRelayed = registry.counter("gateway.bytes.relayed");

        this.firstChunkRelay = registry.timer("gateway.first.chunk.relay");
        this.streamDuration = registry.timer("gateway.stream.duration");

        Gauge.builder("gateway.active.streams", activeStreams, AtomicInteger::get).register(registry);
        Gauge.builder("gateway.admission.active", admissionActive, AtomicInteger::get).register(registry);
        Gauge.builder("gateway.upstream.active", upstreamActive, AtomicInteger::get).register(registry);
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

    /** Diagnostic-only accessors for postflight invariant checks. */
    public int activeStreamsValue() {
        return activeStreams.get();
    }

    public int admissionActiveValue() {
        return admissionActive.get();
    }

    public int upstreamActiveValue() {
        return upstreamActive.get();
    }

    public void recordFirstChunkRelay(long nanos) {
        firstChunkRelay.record(nanos, TimeUnit.NANOSECONDS);
    }

    public void recordStreamDuration(long nanos) {
        streamDuration.record(nanos, TimeUnit.NANOSECONDS);
    }
}
