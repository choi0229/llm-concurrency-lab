package com.llmconcurrencylab.phase4.common;

import java.util.EnumMap;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import org.springframework.stereotype.Component;

/**
 * Common custom metrics, identical names/semantics across M1/M2/M3 (docs/decisions/
 * phase4-metrics-contract.md section 3). Framework-provided metrics (JVM/Tomcat/Reactor Netty)
 * are NOT registered here — those come from Boot Actuator's own auto-configured binders.
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

    /** Must be called exactly once per request, by whichever caller won the terminal CAS. */
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
