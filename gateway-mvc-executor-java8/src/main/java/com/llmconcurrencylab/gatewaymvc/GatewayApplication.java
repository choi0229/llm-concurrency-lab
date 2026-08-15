package com.llmconcurrencylab.gatewaymvc;

import io.prometheus.client.hotspot.DefaultExports;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class GatewayApplication {

    public static void main(String[] args) {
        // Registers jvm_threads_*, jvm_memory_*, process_cpu_seconds_total,
        // process_resident_memory_bytes, ... on the default CollectorRegistry —
        // see monitoring-baseline.md section 1 (scrape overhead already
        // accounted for; these are cheap MXBean reads, not sampling).
        DefaultExports.initialize();
        SpringApplication.run(GatewayApplication.class, args);
    }
}
