package com.llmconcurrencylab.gatewaymvc.webclient;

import java.time.Duration;

import com.llmconcurrencylab.gatewaymvc.admission.AdmissionGate;
import com.llmconcurrencylab.gatewaymvc.config.EnvUtil;
import io.netty.channel.ChannelOption;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.reactive.ReactorClientHttpConnector;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.netty.http.client.HttpClient;
import reactor.netty.resources.ConnectionProvider;
import reactor.netty.resources.LoopResources;

/**
 * WebClient + Reactor Netty ConnectionProvider (docs/decisions/phase3-admission-connection-pool.md
 * §3). application admission (AdmissionGate) is meant to be the PRIMARY limiter — this connection
 * pool's pending-acquire path is a safety net that should read ~0 in normal Functional/Formal
 * operation, not a second capacity limiter behind admission (§3-5 of that ADR).
 *
 * Unit 1 confirmed by direct execution (docs/test-results/phase3/unit1-capability-spikes/
 * connectionprovider-pendingAcquireMaxCount-0-result.txt) that
 * {@code pendingAcquireMaxCount(0)} throws {@code IllegalArgumentException} — true zero-pending
 * fail-fast is not supported by Reactor Netty 1.0.39. So instead: {@code maxConnections} defaults
 * to the admission ceiling (mirroring P3-A's CHAT_BLOCKING_POOL_SIZE default), which makes pending
 * acquisition structurally unreachable in normal operation, and {@code pendingAcquireMaxCount}/
 * {@code pendingAcquireTimeout} are a small positive safety net that should never actually fire.
 *
 * <p><b>Unit 4-0 addition:</b> the {@code HttpClient} now runs on an explicit, dedicated
 * {@link LoopResources} instead of Reactor Netty's implicit global default. Confirmed by direct
 * execution (docs/decisions/phase3-reactor-resource-topology.md) that leaving this unset means a
 * Reactor Netty *server* elsewhere in the same JVM (P3-C's WebFlux server) would silently share
 * event-loop threads with this client — 10 total `reactor-http-nio-*` threads observed instead of
 * 20 in both a raw Reactor Netty spike and an actual Boot 2.7.18 WebFlux spike. P3-B has no server
 * of its own, so this change makes no functional difference here by itself, but it's applied
 * symmetrically to P3-C's WebClient so Experiment A's variable stays clean and Experiment B (P3-B
 * vs P3-C) doesn't pick up an accidental "shares threads with something else" confound on the
 * P3-C side alone.
 */
@Configuration
public class WebClientConfig {

    private static final int PENDING_ACQUIRE_MAX_COUNT_DEFAULT = 1;
    private static final int PENDING_ACQUIRE_TIMEOUT_MS_DEFAULT = 1000;
    private static final int CONNECT_TIMEOUT_MS_DEFAULT = 3000;
    // Low-level safety timeout on the Reactor Netty response — distinct in meaning from
    // CHAT_TOTAL_TIMEOUT_MS (the lifecycle absolute deadline, docs/decisions/
    // phase3-timeout-cancellation.md §1). Set generously so it never fires before the primary
    // deadline mechanism does in normal operation (Unit 3 §14 — must not conflate the two).
    private static final int RESPONSE_TIMEOUT_MS_DEFAULT = 120000;

    @Bean(destroyMethod = "dispose")
    public LoopResources webClientLoopResources() {
        int workerCount = EnvUtil.getInt("WEBCLIENT_LOOP_THREADS", Runtime.getRuntime().availableProcessors());
        return LoopResources.create("gateway-webclient-loop", workerCount, true);
    }

    @Bean(destroyMethod = "dispose")
    public ConnectionProvider webClientConnectionProvider(AdmissionGate admissionGate) {
        int maxConnections = EnvUtil.getInt("WEBCLIENT_MAX_CONNECTIONS", admissionGate.limit());
        int pendingAcquireMaxCount =
                EnvUtil.getInt("WEBCLIENT_PENDING_ACQUIRE_MAX_COUNT", PENDING_ACQUIRE_MAX_COUNT_DEFAULT);
        int pendingAcquireTimeoutMs =
                EnvUtil.getInt("WEBCLIENT_PENDING_ACQUIRE_TIMEOUT_MS", PENDING_ACQUIRE_TIMEOUT_MS_DEFAULT);

        return ConnectionProvider.builder("gateway-webclient-pool")
                .maxConnections(maxConnections)
                .pendingAcquireMaxCount(pendingAcquireMaxCount)
                .pendingAcquireTimeout(Duration.ofMillis(pendingAcquireTimeoutMs))
                .metrics(true) // reactor_netty_connection_provider_* (confirmed names, Unit 1 spike)
                .build();
    }

    @Bean
    public WebClient mockLlmWebClient(ConnectionProvider connectionProvider, LoopResources loopResources) {
        int connectTimeoutMs = EnvUtil.getInt("WEBCLIENT_CONNECT_TIMEOUT_MS", CONNECT_TIMEOUT_MS_DEFAULT);
        int responseTimeoutMs = EnvUtil.getInt("WEBCLIENT_RESPONSE_TIMEOUT_MS", RESPONSE_TIMEOUT_MS_DEFAULT);
        String baseUrl = EnvUtil.getString("MOCK_LLM_BASE_URL", "http://localhost:8000");

        HttpClient httpClient = HttpClient.create(connectionProvider)
                .runOn(loopResources, false) // false: no native (epoll/kqueue) transport artifact on
                                              // this classpath for macOS — see ADR §3.
                .option(ChannelOption.CONNECT_TIMEOUT_MILLIS, connectTimeoutMs)
                .responseTimeout(Duration.ofMillis(responseTimeoutMs))
                .metrics(true, s -> s); // reactor_netty_http_client_* (confirmed names, Unit 1 spike)

        return WebClient.builder()
                .baseUrl(baseUrl)
                .clientConnector(new ReactorClientHttpConnector(httpClient))
                .build();
    }
}
