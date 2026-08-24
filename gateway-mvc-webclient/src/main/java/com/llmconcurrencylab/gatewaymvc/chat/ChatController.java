package com.llmconcurrencylab.gatewaymvc.chat;

import java.io.IOException;
import java.io.PrintWriter;
import java.time.Duration;
import java.util.UUID;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

import javax.servlet.AsyncContext;
import javax.servlet.AsyncEvent;
import javax.servlet.AsyncListener;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

import com.llmconcurrencylab.gatewaymvc.admission.AdmissionGate;
import com.llmconcurrencylab.gatewaymvc.config.EnvUtil;
import com.llmconcurrencylab.gatewaymvc.deadline.AbsoluteDeadline;
import com.llmconcurrencylab.gatewaymvc.deadline.AbsoluteDeadlineExceededException;
import com.llmconcurrencylab.gatewaymvc.metrics.GatewayMetrics;
import com.llmconcurrencylab.gatewaymvc.sse.SseFrameFormatter;
import com.llmconcurrencylab.gatewaymvc.upstream.MockLlmClient;
import com.llmconcurrencylab.gatewaymvc.write.PerStreamWriteChannel;
import org.reactivestreams.Subscription;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;
import reactor.core.publisher.Flux;

/**
 * P3-B control implementation (Unit 3). Architecture (docs/decisions/phase3-mvc-webclient-write-
 * path.md, Unit 3 §4):
 *
 * <pre>
 * Front -> Spring MVC Controller -> Servlet AsyncContext -> admission tryAcquire()
 *   -> WebClient POST /mock/stream -> Reactor Netty SSE receive -> fast non-blocking onNext
 *   -> PerStreamWriteChannel -> shared bounded Servlet write executor
 *   -> PrintWriter.write + flush -> Front
 * </pre>
 *
 * Structural comparison with P3-A ({@code gateway-mvc-blocking-spring5}) is in this module's
 * README — same admission/lifecycle/write-channel/metrics contract, the outbound client
 * (blocking HttpURLConnection + dedicated Platform Thread pool vs. non-blocking WebClient +
 * Reactor Netty event loop, no dedicated pool) is the one thing Experiment A varies.
 */
@RestController
@RequestMapping("/chat")
public class ChatController {

    private static final Logger log = LoggerFactory.getLogger(ChatController.class);

    private static final int PER_STREAM_BUFFER_CAPACITY_DEFAULT = 32; // functional default, not Formal-frozen

    private final AdmissionGate admissionGate;
    private final MockLlmClient mockLlmClient;
    private final GatewayMetrics metrics;
    private final ThreadPoolExecutor servletWriteExecutor;
    private final ScheduledExecutorService deadlineWatchdogExecutor;

    private final int totalTimeoutMs;
    private final int watchdogMarginMs;
    private final int perStreamBufferCapacity;

    @Autowired
    public ChatController(AdmissionGate admissionGate, MockLlmClient mockLlmClient, GatewayMetrics metrics,
            ThreadPoolExecutor servletWriteExecutor, ScheduledExecutorService deadlineWatchdogExecutor) {
        this.admissionGate = admissionGate;
        this.mockLlmClient = mockLlmClient;
        this.metrics = metrics;
        this.servletWriteExecutor = servletWriteExecutor;
        this.deadlineWatchdogExecutor = deadlineWatchdogExecutor;
        this.totalTimeoutMs = EnvUtil.getInt("CHAT_TOTAL_TIMEOUT_MS", 60000);
        this.watchdogMarginMs = EnvUtil.getInt("CHAT_ASYNC_WATCHDOG_MARGIN_MS", 5000);
        this.perStreamBufferCapacity =
                EnvUtil.getInt("SERVLET_PER_STREAM_BUFFER_CAPACITY", PER_STREAM_BUFFER_CAPACITY_DEFAULT);
    }

    @RequestMapping(value = "/stream", method = RequestMethod.POST)
    public void stream(HttpServletRequest request, @RequestBody(required = false) String body) throws IOException {
        final long startNanos = System.nanoTime();
        metrics.requestsStarted.increment();
        final String requestBody = (body == null || body.isEmpty()) ? "{}" : body;
        final String requestId = UUID.randomUUID().toString();

        final AsyncContext asyncContext = request.startAsync();
        // Primary deadline is the application absolute deadline below — this is only the safety
        // watchdog (docs/decisions/phase3-timeout-cancellation.md §5).
        asyncContext.setTimeout(totalTimeoutMs + watchdogMarginMs);
        final HttpServletResponse response = (HttpServletResponse) asyncContext.getResponse();
        response.setContentType("text/event-stream");
        response.setCharacterEncoding("UTF-8");

        final RequestLifecycle lifecycle =
                new RequestLifecycle(requestId, asyncContext, admissionGate, metrics, startNanos);
        metrics.activeStreamsIncrement();
        log.info("[{}] request start", requestId);

        asyncContext.addListener(new AsyncListener() {
            @Override
            public void onComplete(AsyncEvent event) {
                if (lifecycle.tryTerminate("internal_error")) {
                    log.warn("[{}] onComplete won the terminal race (unexpected path)", requestId);
                }
            }

            @Override
            public void onTimeout(AsyncEvent event) {
                // AsyncContext timeout = deadline + safety margin. If THIS fires (rather than the
                // application deadline watchdog), the application-level deadline mechanism failed
                // to fire in time — anomaly, not a normal timeout outcome (ADR §5).
                metrics.watchdogActivated.increment();
                log.warn("[{}] AsyncContext safety watchdog fired ({}ms after start, {}ms past the {}ms "
                                + "application deadline) — application deadline watchdog did not terminate in time",
                        requestId, totalTimeoutMs + watchdogMarginMs, watchdogMarginMs, totalTimeoutMs);
                lifecycle.tryTerminate("timeout");
            }

            @Override
            public void onError(AsyncEvent event) {
                log.warn("[{}] async error: {}", requestId,
                        event.getThrowable() != null ? event.getThrowable().getMessage() : "unknown");
                lifecycle.tryTerminate("client_disconnect");
            }

            @Override
            public void onStartAsync(AsyncEvent event) {
            }
        });

        if (!admissionGate.tryAcquire()) {
            metrics.admissionRejected.increment();
            log.warn("[{}] rejected: admission ceiling reached", requestId);
            try {
                response.setStatus(HttpServletResponse.SC_SERVICE_UNAVAILABLE);
                response.getWriter().write("{\"status\":\"REJECTED\",\"reason\":\"executor_saturated\"}");
            } catch (IOException ignored) {
                // client already gone; nothing more to do
            } finally {
                lifecycle.tryTerminate("rejected");
            }
            return;
        }
        lifecycle.markAdmitted();
        metrics.admissionActiveIncrement();

        final long deadlineNanos = startNanos + TimeUnit.MILLISECONDS.toNanos(totalTimeoutMs);
        long remainingNanosNow = deadlineNanos - System.nanoTime();
        ScheduledFuture<?> deadlineTask = deadlineWatchdogExecutor.schedule(
                () -> lifecycle.tryTerminate("timeout"), Math.max(0, remainingNanosNow), TimeUnit.NANOSECONDS);
        lifecycle.setDeadlineTask(deadlineTask);

        relay(response, requestBody, requestId, lifecycle, deadlineNanos);
    }

    /**
     * Builds the WebClient call and subscribes — non-blocking, returns immediately (the actual
     * connect/read happens on Reactor Netty's own event loop, never this calling thread). Unlike
     * P3-A there is no separate executor submission here: subscribing IS the non-blocking act of
     * starting the outbound call (Unit 3 §4 — no blocking outbound worker exists in this
     * architecture).
     */
    private void relay(HttpServletResponse response, String requestBody, String requestId,
            RequestLifecycle lifecycle, long deadlineNanos) {
        if (lifecycle.isTerminal()) {
            log.warn("[{}] skipped: already terminal before relay started", requestId);
            return;
        }

        PrintWriter writer;
        try {
            writer = response.getWriter();
        } catch (IOException e) {
            lifecycle.tryTerminate("client_disconnect");
            return;
        }
        PerStreamWriteChannel writeChannel =
                new PerStreamWriteChannel(writer, servletWriteExecutor, perStreamBufferCapacity, metrics, lifecycle);
        lifecycle.setWriteChannel(writeChannel);

        lifecycle.markUpstreamActive();
        metrics.upstreamActiveIncrement();

        long remainingNanos = deadlineNanos - System.nanoTime();
        Duration remaining = Duration.ofNanos(Math.max(1, remainingNanos));

        // AbsoluteDeadline.apply() uses the SAME deadlineNanos-derived remaining budget as the
        // lifecycle watchdog scheduled in stream() — never two independently-computed durations
        // (Unit 3 §10). This lets the Reactor subscription self-cancel at the deadline instead of
        // relying solely on the watchdog's external dispose() call — the capability P3-A's
        // blocking read lacks (Unit 2 Smoke B: disconnect()/interrupt() did not promptly unblock
        // an in-flight blocked readLine(); see docs/test-results/phase3/unit2-p3a-functional/).
        Flux<ServerSentEvent<String>> upstream = mockLlmClient.openStream(requestBody);
        Flux<ServerSentEvent<String>> withDeadline = AbsoluteDeadline.apply(upstream, remaining);

        withDeadline
                // doFinally guarantees exactly-once regardless of which terminal signal (complete/
                // error/cancel) fires — this is the sole place upstreamActiveDecrement() runs, so
                // it can't be skipped or double-counted across the different callback paths below.
                .doFinally(signalType -> metrics.upstreamActiveDecrement())
                .subscribe(
                        sse -> onUpstreamEvent(sse, writeChannel, lifecycle, requestId),
                        error -> onUpstreamError(error, lifecycle, requestId),
                        () -> onUpstreamComplete(writeChannel, lifecycle, requestId),
                        subscription -> onUpstreamSubscribe(subscription, lifecycle, requestId));
    }

    /**
     * Registers the cancellable handle before requesting any items — Reactive Streams guarantees
     * onSubscribe happens-before any onNext/onComplete/onError, so this closes the same race
     * P3-A's setConnection()/setOutboundFuture() close (docs/decisions/phase3-timeout-
     * cancellation.md §7): if the lifecycle is already terminal by the time this fires (e.g. the
     * deadline watchdog won before the WebClient call even started), the subscription is cancelled
     * immediately instead of being left to run. A custom subscriptionConsumer MUST call request()
     * itself — Reactor does not do it implicitly once you provide one.
     */
    private void onUpstreamSubscribe(Subscription subscription, RequestLifecycle lifecycle, String requestId) {
        lifecycle.setUpstreamSubscription(subscription::cancel);
        subscription.request(Long.MAX_VALUE);
    }

    /**
     * Reactor Netty event-loop thread — fast, non-blocking only (docs/decisions/
     * phase3-mvc-webclient-write-path.md §3, Unit 3 §6): frame formatting + a bounded-queue
     * offer(). Never touches PrintWriter directly.
     */
    private void onUpstreamEvent(ServerSentEvent<String> sse, PerStreamWriteChannel writeChannel,
            RequestLifecycle lifecycle, String requestId) {
        lifecycle.markStreaming();
        String frame = SseFrameFormatter.format(sse);
        boolean accepted = writeChannel.offer(frame);
        if (!accepted) {
            // Either already terminal, or offer() itself just triggered write_overflow
            // termination (PerStreamWriteChannel.offer()'s javadoc) — either way, the channel/
            // lifecycle has already done everything needed; nothing further to do here (Unit 3
            // §17 — the event-loop must never wait for buffer space).
            log.debug("[{}] frame dropped post-terminal/overflow", requestId);
        }
    }

    /**
     * Natural upstream EOF. Do NOT call tryTerminate("completed") directly here — mirrors the real
     * bug found in P3-A's Smoke A (docs/test-results/phase3/unit2-p3a-functional/SUMMARY.md): the
     * final SSE frame may still be sitting in the write channel's buffer, not yet actually written.
     * markProducerDone() defers "completed" until the buffer has actually drained empty.
     *
     * The admission permit, however, IS released here — at natural upstream EOF, not at
     * response-drain-complete (Unit 6 admission-semantics unification: docs/decisions/
     * phase3-admission-semantics-unification.md). Downstream (Mock LLM) capacity is freed the
     * moment the upstream call itself is done; gateway_active_streams and the AsyncContext
     * completion still wait for the write channel to actually drain.
     */
    private void onUpstreamComplete(PerStreamWriteChannel writeChannel, RequestLifecycle lifecycle, String requestId) {
        lifecycle.releaseAdmissionPermit();
        writeChannel.markProducerDone();
    }

    private void onUpstreamError(Throwable error, RequestLifecycle lifecycle, String requestId) {
        if (lifecycle.isTerminal()) {
            log.warn("[{}] upstream error after already terminal (known race): {}", requestId, error.toString());
            return;
        }
        if (error instanceof AbsoluteDeadlineExceededException) {
            lifecycle.tryTerminate("timeout");
        } else {
            log.warn("[{}] upstream error: {}", requestId, error.toString());
            lifecycle.tryTerminate("upstream_error");
        }
    }
}
