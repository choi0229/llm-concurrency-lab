package com.llmconcurrencylab.phase4.webflux;

import java.time.Duration;
import java.util.concurrent.TimeoutException;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.MediaType;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.reactive.function.server.ServerRequest;
import org.springframework.web.reactive.function.server.ServerResponse;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;
import reactor.core.publisher.SignalType;

/**
 * M3 — native reactive handler (docs/test-plan/phase4-design.md section 7). No AsyncContext, no
 * Servlet API, no PrintWriter, no application write executor, no HttpURLConnection, no
 * boundedElastic blocking bridge, no application admission gate.
 */
@Component
public class ChatHandler {

    private static final Logger log = LoggerFactory.getLogger(ChatHandler.class);

    private final WebClient webClient;
    private final GatewayMetrics metrics;
    private final RequestIdGenerator requestIdGenerator;
    private final int totalTimeoutMs;

    public ChatHandler(WebClient webClient, GatewayMetrics metrics, RequestIdGenerator requestIdGenerator) {
        this.webClient = webClient;
        this.metrics = metrics;
        this.requestIdGenerator = requestIdGenerator;
        this.totalTimeoutMs = EnvUtil.getInt("CHAT_TOTAL_TIMEOUT_MS", 60000);
    }

    public Mono<ServerResponse> stream(ServerRequest request) {
        String requestId = requestIdGenerator.next();
        return request.bodyToMono(String.class)
                .defaultIfEmpty("{}")
                // "request body decode success -> application lifecycle start"
                // (docs/test-plan/phase4-design.md section 9), applied identically to M1/M2.
                .flatMap(body -> handle(requestId, body));
    }

    private Mono<ServerResponse> handle(String requestId, String requestBody) {
        long startNanos = System.nanoTime();
        metrics.requestStarted();

        AtomicReference<Outcome> terminalOutcome = new AtomicReference<>();
        AtomicBoolean deadlineFired = new AtomicBoolean(false);
        AtomicReference<Throwable> lastError = new AtomicReference<>();
        AtomicBoolean sawFirstEvent = new AtomicBoolean(false);

        Flux<ServerSentEvent<String>> upstream = webClient.post()
                .uri("/mock/stream")
                .contentType(MediaType.APPLICATION_JSON)
                .accept(MediaType.TEXT_EVENT_STREAM)
                .bodyValue(requestBody)
                .retrieve()
                .bodyToFlux(new ParameterizedTypeReference<ServerSentEvent<String>>() {
                })
                .doOnSubscribe(s -> metrics.upstreamStarted())
                .doOnNext(sse -> {
                    if (sawFirstEvent.compareAndSet(false, true)) {
                        metrics.firstUpstreamEvent(System.nanoTime() - startNanos);
                    }
                    String data = sse.data() == null ? "" : sse.data();
                    String event = sse.event() == null ? "message" : sse.event();
                    metrics.bytesRelayed(event.length() + data.length());
                })
                .doFinally(sig -> metrics.upstreamEnded());

        // Absolute deadline operator — "Candidate C" pattern validated in docs/decisions/
        // phase3-timeout-cancellation.md section 3-3: the deadline signal must complete (never
        // error) so takeUntilOther correctly cancels the main source in both directions; the real
        // TimeoutException is injected afterward via concatWith.
        Mono<Void> deadlineCompletionSignal = Mono.delay(Duration.ofMillis(totalTimeoutMs))
                .doOnNext(t -> deadlineFired.set(true))
                .then();

        Flux<ServerSentEvent<String>> withDeadline = upstream
                .takeUntilOther(deadlineCompletionSignal)
                .concatWith(Flux.defer(() -> deadlineFired.get()
                        ? Flux.error(new TimeoutException("absolute-deadline-exceeded"))
                        : Flux.empty()))
                .doOnError(lastError::set)
                .doFinally(sig -> {
                    Outcome outcome = mapSignal(sig, deadlineFired.get(), lastError.get());
                    if (terminalOutcome.compareAndSet(null, outcome)) {
                        metrics.requestTerminal(outcome);
                        metrics.requestDuration(System.nanoTime() - startNanos);
                        log.debug("[{}] terminal outcome={}", requestId, outcome.label());
                    }
                });
        // A TimeoutException/upstream error is deliberately left to propagate as onError on this
        // Flux — WebFlux maps an unhandled body-Flux error occurring before response commit to a
        // 500 response by default (docs/decisions/phase3-metrics-contract.md section 8-3 F5
        // finding). Phase 4 keeps this native WebFlux behavior rather than forcing M1/M2-style
        // early-committed 200 semantics onto M3 (docs/test-plan/phase4-design.md section 7).

        return ServerResponse.ok()
                .contentType(MediaType.TEXT_EVENT_STREAM)
                .body(withDeadline, new ParameterizedTypeReference<ServerSentEvent<String>>() {
                });
    }

    private Outcome mapSignal(SignalType signal, boolean deadlineFired, Throwable error) {
        if (signal == SignalType.CANCEL) {
            return Outcome.CLIENT_DISCONNECT;
        }
        if (signal == SignalType.ON_ERROR) {
            if (deadlineFired || error instanceof TimeoutException) {
                return Outcome.TIMEOUT;
            }
            return Outcome.UPSTREAM_ERROR;
        }
        return Outcome.COMPLETED;
    }
}
