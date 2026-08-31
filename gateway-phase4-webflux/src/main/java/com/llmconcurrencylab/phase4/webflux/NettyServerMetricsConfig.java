package com.llmconcurrencylab.phase4.webflux;

import org.springframework.boot.reactor.netty.NettyServerCustomizer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Enables {@code reactor_netty_http_server_*} metrics — NOT on by default even with Actuator +
 * Micrometer on the classpath (Unit 2 finding; docs/decisions/phase4-metrics-contract.md
 * section 4 candidate, previously unconfirmed even in Phase 3's own P3-C capability spike). No
 * custom LoopResources are attached here — only the metrics recorder is enabled (docs/test-plan/
 * phase4-design.md section 0-3: default Reactor Netty resource topology).
 */
@Configuration
public class NettyServerMetricsConfig {

    @Bean
    public NettyServerCustomizer nettyServerMetricsCustomizer() {
        return httpServer -> httpServer.metrics(true, uri -> "/chat/stream");
    }
}
