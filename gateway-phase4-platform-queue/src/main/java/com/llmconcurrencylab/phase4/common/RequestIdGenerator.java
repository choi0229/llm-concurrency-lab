package com.llmconcurrencylab.phase4.common;

import java.util.concurrent.atomic.AtomicLong;

import org.springframework.stereotype.Component;

/**
 * Lightweight monotonic request-id generator — log correlation only, never a metric tag
 * (docs/decisions/phase4-metrics-contract.md section 15). AtomicLong rather than
 * UUID.randomUUID()/SecureRandom: Phase 4 Unit 2 design brief section 6 calls out Phase 3's
 * experience of random-UUID generation becoming an unexpected blocking point on a Reactor
 * event-loop thread — Phase 4 avoids the class of problem entirely rather than re-verifying it
 * per model.
 */
@Component
public class RequestIdGenerator {

    private final AtomicLong counter = new AtomicLong();

    public String next() {
        return "p4-" + counter.incrementAndGet();
    }
}
