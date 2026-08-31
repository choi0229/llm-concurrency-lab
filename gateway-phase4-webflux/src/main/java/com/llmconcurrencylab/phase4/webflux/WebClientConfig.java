package com.llmconcurrencylab.phase4.webflux;

import java.time.Duration;

import io.netty.channel.ChannelOption;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.reactive.ReactorClientHttpConnector;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.netty.http.client.HttpClient;
import reactor.netty.resources.ConnectionProvider;

/**
 * M3 WebClient — headroom-controlled ConnectionProvider only, NO custom LoopResources
 * (docs/test-plan/phase4-design.md section 0-3 / section 7-1: default Boot/Reactor resource
 * topology).
 *
 * <p><b>WEBCLIENT_MAX_CONNECTIONS default here (100) is FUNCTIONAL_ONLY_NOT_PHASE4_CAPACITY_CONFIG</b>
 * — the real headroom-controlled value (>= 1.25x planned Gateway max concurrency) can only be set
 * after Unit 3 control calibration determines that target range (docs/test-plan/phase4-design.md
 * section 7-1, docs/decisions/phase4-resource-safety-policy.md section 4). Formal/Screening
 * harnesses MUST override this via env, not rely on this default.
 */
@Configuration
public class WebClientConfig {

    private static final Logger log = LoggerFactory.getLogger(WebClientConfig.class);

    @Bean
    public WebClient mockLlmWebClient() {
        int maxConnections = EnvUtil.getInt("WEBCLIENT_MAX_CONNECTIONS", 100);
        int pendingAcquireMaxCount = EnvUtil.getInt("WEBCLIENT_PENDING_ACQUIRE_MAX_COUNT", 200);
        int pendingAcquireTimeoutMs = EnvUtil.getInt("WEBCLIENT_PENDING_ACQUIRE_TIMEOUT_MS", 10000);
        int connectTimeoutMs = EnvUtil.getInt("WEBCLIENT_CONNECT_TIMEOUT_MS", 3000);
        String baseUrl = EnvUtil.getString("MOCK_LLM_BASE_URL", "http://127.0.0.1:8000");

        log.info("WebClient config: baseUrl={} maxConnections={} (FUNCTIONAL_ONLY_NOT_PHASE4_CAPACITY_CONFIG "
                        + "unless overridden) pendingAcquireMaxCount={} pendingAcquireTimeoutMs={} connectTimeoutMs={}",
                baseUrl, maxConnections, pendingAcquireMaxCount, pendingAcquireTimeoutMs, connectTimeoutMs);

        ConnectionProvider provider = ConnectionProvider.builder("phase4-webflux-pool")
                .maxConnections(maxConnections)
                .pendingAcquireMaxCount(pendingAcquireMaxCount)
                .pendingAcquireTimeout(Duration.ofMillis(pendingAcquireTimeoutMs))
                .metrics(true)
                .build();

        // No .runOn(customLoopResources) — default Reactor Netty event-loop group only.
        // .metrics(true) is required for reactor_netty_connection_provider_*/http_client_* to
        // appear at all (Unit 2 finding: NOT exposed by default even with Actuator+Micrometer on
        // the classpath, matching Phase 3's docs/decisions/phase3-metrics-contract.md section 2-3
        // precedent under a different Reactor Netty version).
        HttpClient httpClient = HttpClient.create(provider)
                .metrics(true, uri -> "/mock/stream")
                .option(ChannelOption.CONNECT_TIMEOUT_MILLIS, connectTimeoutMs);

        return WebClient.builder()
                .baseUrl(baseUrl)
                .clientConnector(new ReactorClientHttpConnector(httpClient))
                .build();
    }
}
