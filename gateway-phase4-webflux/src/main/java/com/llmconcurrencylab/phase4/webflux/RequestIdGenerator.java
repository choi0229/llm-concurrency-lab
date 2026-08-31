package com.llmconcurrencylab.phase4.webflux;

import java.util.concurrent.atomic.AtomicLong;

import org.springframework.stereotype.Component;

/** Same rationale as M1/M2's generator (docs/decisions/phase4-metrics-contract.md section 15). */
@Component
public class RequestIdGenerator {

    private final AtomicLong counter = new AtomicLong();

    public String next() {
        return "p4-" + counter.incrementAndGet();
    }
}
