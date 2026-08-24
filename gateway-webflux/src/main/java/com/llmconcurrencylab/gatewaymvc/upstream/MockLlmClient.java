package com.llmconcurrencylab.gatewaymvc.upstream;

import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.MediaType;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;

/**
 * WebClient counterpart of P3-A's MockLlmClient (java.net.HttpURLConnection). Same endpoint/
 * request shape (docs/test-plan/phase3-design.md §3-2) — POST /mock/stream, request body passed
 * through unchanged. The connection/timeout knobs live in WebClientConfig instead of here (the
 * WebClient/HttpClient instance is already fully configured when it's injected).
 */
@Component
public class MockLlmClient {

    private final WebClient webClient;

    public MockLlmClient(WebClient mockLlmWebClient) {
        this.webClient = mockLlmWebClient;
    }

    /** Non-blocking — returns immediately with a cold Flux; nothing happens until it's subscribed to. */
    public Flux<ServerSentEvent<String>> openStream(String requestBodyJson) {
        return webClient.post()
                .uri("/mock/stream")
                .contentType(MediaType.APPLICATION_JSON)
                .accept(MediaType.TEXT_EVENT_STREAM)
                .bodyValue(requestBodyJson)
                .retrieve()
                .bodyToFlux(new ParameterizedTypeReference<ServerSentEvent<String>>() {
                });
    }
}
