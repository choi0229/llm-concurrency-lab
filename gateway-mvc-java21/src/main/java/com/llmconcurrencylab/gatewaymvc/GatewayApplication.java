package com.llmconcurrencylab.gatewaymvc;

import io.prometheus.client.hotspot.DefaultExports;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class GatewayApplication {

    public static void main(String[] args) {
        // Registers jvm_threads_current/jvm_threads_peak (ThreadMXBean-backed —
        // see docs/test-plan/phase2-design.md section 8-1, "JVM live/peak
        // platform thread count"), jvm_memory_*, jvm_gc_collection_seconds,
        // process_cpu_seconds_total, ... on the default CollectorRegistry.
        // Same mechanism Phase 1 used (monitoring-baseline.md section 1) —
        // no custom carrier/thread-count code needed for these two metrics.
        DefaultExports.initialize();
        SpringApplication.run(GatewayApplication.class, args);
    }
}
