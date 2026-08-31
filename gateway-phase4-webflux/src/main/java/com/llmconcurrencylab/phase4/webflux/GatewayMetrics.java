package com.llmconcurrencylab.phase4.webflux;

import java.util.EnumMap;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import org.springframework.stereotype.Component;

/**
 * Same common metric names/semantics as M1/M2 (docs/decisions/phase4-metrics-contract.md
 * section 3) — semantic parity, not source parity (docs/test-plan/phase4-design.md section 43).
 */
@Component
public class GatewayMetrics {

    private final Counter requestsStarted;
    private final Map<Outcome, Counter> requestsByOutcome = new EnumMap<>(Outcome.class);
    private final AtomicInteger activeRequests = new AtomicInteger();
    private final AtomicInteger upstreamActive = new AtomicInteger();
    private final Counter bytesRelayed;
    private final Timer firstUpstreamEvent;
    private final Timer requestDuration;

    public GatewayMetrics(MeterRegistry registry) {
        this.requestsStarted = registry.counter("gateway.requests.started");
        for (Outcome outcome : Outcome.values()) {
            requestsByOutcome.put(outcome, registry.counter("gateway.requests", "outcome", outcome.label()));
        }
        registry.gauge("gateway.active.requests", activeRequests);
        registry.gauge("gateway.upstream.active", upstreamActive);
        this.bytesRelayed = registry.counter("gateway.bytes.relayed");
        this.firstUpstreamEvent = registry.timer("gateway.first.upstream.event");
        this.requestDuration = registry.timer("gateway.request.duration");
    }

    public void requestStarted() {
        requestsStarted.increment();
        activeRequests.incrementAndGet();
    }

    public void requestTerminal(Outcome outcome) {
        requestsByOutcome.get(outcome).increment();
        activeRequests.decrementAndGet();
    }

    public void upstreamStarted() {
        upstreamActive.incrementAndGet();
    }

    public void upstreamEnded() {
        upstreamActive.decrementAndGet();
    }

    public void bytesRelayed(long n) {
        bytesRelayed.increment(n);
    }

    public void firstUpstreamEvent(long elapsedNanos) {
        firstUpstreamEvent.record(elapsedNanos, TimeUnit.NANOSECONDS);
    }

    public void requestDuration(long elapsedNanos) {
        requestDuration.record(elapsedNanos, TimeUnit.NANOSECONDS);
    }

    public int activeRequestsValue() {
        return activeRequests.get();
    }

    public int upstreamActiveValue() {
        return upstreamActive.get();
    }
}
