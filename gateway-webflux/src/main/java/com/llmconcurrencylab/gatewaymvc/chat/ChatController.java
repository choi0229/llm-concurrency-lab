package com.llmconcurrencylab.gatewaymvc.chat;

import java.time.Duration;
import java.util.UUID;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.TimeUnit;

import com.llmconcurrencylab.gatewaymvc.admission.AdmissionGate;
import com.llmconcurrencylab.gatewaymvc.config.EnvUtil;
import com.llmconcurrencylab.gatewaymvc.deadline.AbsoluteDeadline;
import com.llmconcurrencylab.gatewaymvc.deadline.AbsoluteDeadlineExceededException;
import com.llmconcurrencylab.gatewaymvc.metrics.GatewayMetrics;
import com.llmconcurrencylab.gatewaymvc.sse.SseFrameFormatter;
import com.llmconcurrencylab.gatewaymvc.upstream.MockLlmClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.web.reactive.function.server.RouterFunction;
import org.springframework.web.reactive.function.server.RouterFunctions;
import org.springframework.web.reactive.function.server.ServerRequest;
import org.springframework.web.reactive.function.server.ServerResponse;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

/**
 * P3-C control implementation (Unit 4). Architecture (Unit 4 §2):
 *
 * <pre>
 * Front -> Reactor Netty Server -> WebFlux Controller -> non-blocking admission
 *   -> WebClient -> Mock LLM SSE Flux -> SSE response Flux -> Reactor Netty Server -> Front
 * </pre>
 *
 * No {@code AsyncContext}, no {@code PrintWriter}, no Servlet write executor, no
 * {@code PerStreamWriteChannel} — the response body Flux built here IS the one continuous
 * reactive chain WebFlux subscribes to when writing the HTTP response; there is no separate
 * buffering/execution layer decoupling "upstream received a frame" from "frame reached the
 * client" the way P3-A/B's Servlet write path does. That absence is the point of Experiment B.
 *
 * <p><b>WebFlux functional routing, not an annotated {@code @RestController} — found by Smoke A,
 * not by inspection.</b> Admission-rejected (plain JSON) and success (SSE {@code Flux}) genuinely
 * need different body shapes, so this returns {@code Mono<ServerResponse>} (Unit 4 §7's suggested
 * candidate) rather than a generic {@code ResponseEntity<?>} (which risks losing the
 * {@code ServerSentEvent} element type to erasure and picking the wrong message writer). Two
 * attempts at annotated-controller return types both failed functional Smoke A before this one:
 * {@code @RestController} implies {@code @ResponseBody}, which routed the {@code ServerResponse}
 * object itself through Jackson instead of treating it as an already-built response
 * ({@code CodecException: No serializer found for ... DefaultEntityResponse}, connection aborted
 * mid-stream, 200 already committed); plain {@code @Controller} (no implicit
 * {@code @ResponseBody}) then fell through to Spring's default view-name resolution instead
 * ({@code IllegalStateException: Could not resolve view with name 'chat/stream'}) — annotated
 * {@code @RequestMapping} methods returning bare {@code ServerResponse} are not reliably
 * recognized in this Boot/Spring version's default {@code HandlerResultHandler} chain. WebFlux's
 * functional {@code RouterFunction}/{@code HandlerFunction} model is {@code ServerResponse}'s
 * actual native, unambiguous use case, so this is what ships.
 */
@Configuration
public class ChatController {

    private static final Logger log = LoggerFactory.getLogger(ChatController.class);

    private static final String REJECTED_BODY = "{\"status\":\"REJECTED\",\"reason\":\"executor_saturated\"}";

    private final AdmissionGate admissionGate;
    private final MockLlmClient mockLlmClient;
    private final GatewayMetrics metrics;
    private final int totalTimeoutMs;

    @Autowired
    public ChatController(AdmissionGate admissionGate, MockLlmClient mockLlmClient, GatewayMetrics metrics) {
        this.admissionGate = admissionGate;
        this.mockLlmClient = mockLlmClient;
        this.metrics = metrics;
        this.totalTimeoutMs = EnvUtil.getInt("CHAT_TOTAL_TIMEOUT_MS", 60000);
    }

    @Bean
    public RouterFunction<ServerResponse> chatRoutes() {
        return RouterFunctions.route()
                .POST("/chat/stream", this::stream)
                .build();
    }

    /**
     * Unit 5 §1 lifecycle-start parity correction: P3-A/B's {@code @RequestBody String body}
     * parameter is resolved by Spring MVC's argument-resolver machinery *before* the handler
     * method body ever runs — i.e. body decode always completes strictly before
     * {@code startNanos}/{@code requests_started}/admission/{@code deadlineNanos} in those two
     * modules (verified against their actual source, not assumed). This method used to compute
     * all of those *before* subscribing to {@code request.bodyToMono(...)}, which — because
     * WebFlux's body read is itself asynchronous/lazy — meant P3-C's admission ceiling and
     * absolute deadline could start counting before the request body had even been read, unlike
     * P3-A/B. Fixed by moving the whole lifecycle-start sequence inside the
     * {@code flatMap(requestBody -> ...)} so it only runs once body decode has actually
     * completed, aligning the three implementations on the same T0 definition: "request body
     * decode succeeded, and the Gateway application lifecycle actually began" (docs/test-plan/
     * phase3-design.md §5-1). A request whose body never finishes decoding (a transport-level
     * failure) never reaches this flatMap at all, so it is correctly excluded from
     * {@code gateway_requests_started_total} here — the same as a request that never reaches
     * P3-A/B's handler method body for the same class of reason.
     */
    public Mono<ServerResponse> stream(ServerRequest request) {
        return request.bodyToMono(String.class)
                .defaultIfEmpty("{}")
                .flatMap(requestBody -> {
                    final long startNanos = System.nanoTime();
                    metrics.requestsStarted.increment();
                    final String requestId = fastRandomRequestId();

                    // Admission is a plain non-blocking call, resolved here — before any
                    // upstream/response Publisher for this request is built or subscribed, so a
                    // reject can never race a 200 response being committed first (Unit 4 §7).
                    if (!admissionGate.tryAcquire()) {
                        metrics.admissionRejected.increment();
                        metrics.recordOutcome("rejected");
                        log.warn("[{}] rejected: admission ceiling reached", requestId);
                        return ServerResponse.status(HttpStatus.SERVICE_UNAVAILABLE)
                                .contentType(MediaType.APPLICATION_JSON)
                                .bodyValue(REJECTED_BODY);
                    }

                    final RequestLifecycle lifecycle =
                            new RequestLifecycle(requestId, admissionGate, metrics, startNanos);
                    lifecycle.markAdmitted();
                    metrics.admissionActiveIncrement();
                    metrics.activeStreamsIncrement();
                    log.info("[{}] request start", requestId);

                    final long deadlineNanos = startNanos + TimeUnit.MILLISECONDS.toNanos(totalTimeoutMs);

                    // Flux.defer(): the remaining-budget Duration for AbsoluteDeadline is computed
                    // at actual SUBSCRIPTION time (when WebFlux starts writing the response), not
                    // at this point — deadlineNanos itself is the single fixed source either way
                    // (Unit 4 §13/§14), this only avoids a small precision gap if subscription is
                    // delayed relative to when this handler returns.
                    Flux<ServerSentEvent<String>> upstream = Flux.defer(() -> {
                        Duration remaining = Duration.ofNanos(Math.max(1, deadlineNanos - System.nanoTime()));
                        return AbsoluteDeadline.apply(mockLlmClient.openStream(requestBody), remaining);
                    });
                    Flux<ServerSentEvent<String>> responseFlux =
                            attachLifecycle(upstream, lifecycle, metrics, requestId);

                    return ServerResponse.ok()
                            .contentType(MediaType.TEXT_EVENT_STREAM)
                            .body(responseFlux, new ParameterizedTypeReference<ServerSentEvent<String>>() {
                            });
                });
    }

    /**
     * Unit 5.5 D1 finding: {@link UUID#randomUUID()} was used here originally (matching P3-A/B).
     * A BlockHound diagnostic run against this exact code caught a real violation —
     * {@code BlockingOperationError: Blocking call! java.io.FileInputStream#readBytes} — on a
     * {@code reactor-http-nio-*} thread, with the full stack trace tracing straight through
     * {@code UUID.randomUUID() -> SecureRandom.nextBytes() -> sun.security.provider.NativePRNG ->
     * FileInputStream.read()} (Java 8's default {@code SecureRandom} provider reads from
     * {@code /dev/urandom} synchronously on every call, unlike some newer JDKs' buffered
     * implementations). This is not classloading/JIT warm-up noise — it fires on every request
     * that reaches this line, deterministically, because {@code requestId} generation happens
     * inside the reactive chain (moved there by the Unit 5 §1 lifecycle-start-parity fix, which
     * requires it run after body decode). P3-A/B have the identical `UUID.randomUUID()` call, but
     * on a Servlet/Tomcat thread — not one BlockHound marks non-blocking, so it was never flagged
     * there and was not changed. {@code requestId} is a log-correlation identifier only, never
     * used for security or cross-request uniqueness guarantees beyond "distinguish concurrent
     * requests in logs" (docs/test-plan/phase3-design.md §8), so a {@link ThreadLocalRandom}-based
     * construction (no {@code SecureRandom}, no file I/O, still UUID-shaped) is a safe, minimal,
     * non-architectural fix — re-verified clean by the same BlockHound diagnostic afterward
     * (docs/test-results/phase3/unit5.5-diagnostics/blockhound/p3c-normal/).
     */
    private static String fastRandomRequestId() {
        ThreadLocalRandom random = ThreadLocalRandom.current();
        return new UUID(random.nextLong(), random.nextLong()).toString();
    }

    /**
     * Wires terminal-outcome classification and admission/metric bookkeeping onto an upstream SSE
     * Flux. Package-visible and taking the upstream Flux as a parameter specifically so
     * {@code ChatControllerLifecycleWiringTest} can exercise every required race (Unit 4 §27)
     * against a controllable fake upstream, without needing a full Spring/WebClient/Netty stack.
     */
    static Flux<ServerSentEvent<String>> attachLifecycle(Flux<ServerSentEvent<String>> upstream,
            RequestLifecycle lifecycle, GatewayMetrics metrics, String requestId) {
        return upstream
                .doOnSubscribe(s -> metrics.upstreamActiveIncrement())
                .doOnNext(sse -> {
                    lifecycle.recordFirstChunkIfNeeded();
                    metrics.bytesRelayed.increment(SseFrameFormatter.format(sse).length());
                })
                // Each of these three is the "subject/cause" that decides the outcome (Unit 4
                // §12) — never a bare doFinally(SignalType) trying to infer cause after the fact.
                //
                // releaseAdmissionPermit() is called explicitly here, at the actual upstream
                // onComplete boundary (Unit 6 admission-semantics unification: docs/decisions/
                // phase3-admission-semantics-unification.md) — mirroring P3-A/B's "release at
                // natural upstream EOF" call site rather than leaving it as an implicit side effect
                // buried inside tryTerminate(). Downstream (Mock LLM) capacity is freed the moment
                // the upstream Flux itself completes; gateway_active_streams still waits for the
                // full terminal transition below.
                .doOnComplete(() -> {
                    lifecycle.releaseAdmissionPermit();
                    lifecycle.tryTerminate("completed");
                })
                .doOnError(e -> {
                    if (e instanceof AbsoluteDeadlineExceededException) {
                        lifecycle.tryTerminate("timeout");
                    } else {
                        log.warn("[{}] upstream error: {}", requestId, e.toString());
                        lifecycle.tryTerminate("upstream_error");
                    }
                })
                // Reachable here only from the Front connection going away / WebFlux's response
                // writer cancelling its subscription to this chain — admission rejection never
                // reaches this Flux at all (resolved above, before this Flux exists), and the
                // deadline surfaces as doOnError (AbsoluteDeadlineExceededException), not a raw
                // cancel — see AbsoluteDeadline's javadoc. So CANCEL reaching here has exactly one
                // remaining explanation given this pipeline's shape, not an unconditional guess
                // (Unit 4 §12's warning against blindly mapping CANCEL -> client_disconnect).
                .doOnCancel(() -> lifecycle.tryTerminate("client_disconnect"))
                .doFinally(signalType -> metrics.upstreamActiveDecrement());
    }
}
