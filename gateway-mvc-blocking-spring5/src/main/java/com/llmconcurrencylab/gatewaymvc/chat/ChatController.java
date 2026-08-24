package com.llmconcurrencylab.gatewaymvc.chat;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.PrintWriter;
import java.net.HttpURLConnection;
import java.net.SocketTimeoutException;
import java.nio.charset.StandardCharsets;
import java.util.UUID;
import java.util.concurrent.Future;
import java.util.concurrent.RejectedExecutionException;
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
import com.llmconcurrencylab.gatewaymvc.metrics.GatewayMetrics;
import com.llmconcurrencylab.gatewaymvc.sse.SseFrameReader;
import com.llmconcurrencylab.gatewaymvc.upstream.MockLlmClient;
import com.llmconcurrencylab.gatewaymvc.write.PerStreamWriteChannel;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;

/**
 * P3-A control implementation (Unit 2). Architecture (docs/decisions/phase3-mvc-webclient-write-
 * path.md, Unit 2 §2):
 *
 * <pre>
 * Front -> Spring MVC Controller -> Servlet AsyncContext -> admission tryAcquire()
 *   -> blocking outbound ThreadPoolExecutor -> HttpURLConnection -> Mock LLM SSE
 *   -> BufferedReader/readLine -> SSE frame parser -> PerStreamWriteChannel
 *   -> shared bounded Servlet write executor -> PrintWriter.write + flush -> Front
 * </pre>
 *
 * API/SSE contract reused byte-for-byte from Phase 1/2 (docs/test-plan/phase3-design.md §3) — this
 * class does not invent new endpoints, request/response shapes, or SSE framing.
 */
@RestController
@RequestMapping("/chat")
public class ChatController {

    private static final Logger log = LoggerFactory.getLogger(ChatController.class);

    private static final int PER_STREAM_BUFFER_CAPACITY_DEFAULT = 32; // functional default, not Formal-frozen

    private final AdmissionGate admissionGate;
    private final MockLlmClient mockLlmClient;
    private final GatewayMetrics metrics;
    private final ThreadPoolExecutor blockingOutboundExecutor;
    private final ThreadPoolExecutor servletWriteExecutor;
    private final ScheduledExecutorService deadlineWatchdogExecutor;

    private final int totalTimeoutMs;
    private final int watchdogMarginMs;
    private final int perStreamBufferCapacity;

    @Autowired
    public ChatController(AdmissionGate admissionGate, MockLlmClient mockLlmClient, GatewayMetrics metrics,
            ThreadPoolExecutor blockingOutboundExecutor, ThreadPoolExecutor servletWriteExecutor,
            ScheduledExecutorService deadlineWatchdogExecutor) {
        this.admissionGate = admissionGate;
        this.mockLlmClient = mockLlmClient;
        this.metrics = metrics;
        this.blockingOutboundExecutor = blockingOutboundExecutor;
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
                // Same "unexpected but harmless if we already won" pattern as Phase 1/2's
                // AsyncListener.onComplete (ChatController.java in gateway-mvc-java21) — normally a
                // no-op since relay()'s own tryTerminate() already completed the AsyncContext.
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
        ScheduledFuture<?> deadlineTask = deadlineWatchdogExecutor.schedule(() -> {
            log.info("[{}] deadline watchdog firing at +{}ms", requestId,
                    TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - startNanos));
            lifecycle.tryTerminate("timeout");
        }, Math.max(0, remainingNanosNow), TimeUnit.NANOSECONDS);
        lifecycle.setDeadlineTask(deadlineTask);

        try {
            Future<?> outboundFuture = blockingOutboundExecutor.submit(
                    () -> relay(response, requestBody, requestId, lifecycle, deadlineNanos));
            lifecycle.setOutboundFuture(outboundFuture);
        } catch (RejectedExecutionException e) {
            // Admission succeeded but the blocking outbound executor rejected — per Unit 2 §7 this
            // is a resource/config invariant violation (pool size should always equal the admission
            // ceiling), not a normal overload signal.
            metrics.blockingExecutorRejected.increment();
            log.error("[{}] blocking outbound executor rejected a task after admission succeeded "
                    + "(pool size / admission ceiling mismatch?)", requestId, e);
            lifecycle.tryTerminate("internal_error");
        }
    }

    private void relay(HttpServletResponse response, String requestBody, String requestId,
            RequestLifecycle lifecycle, long deadlineNanos) {
        long taskStartNanos = System.nanoTime();
        try {
            long remainingNanosAtStart = deadlineNanos - System.nanoTime();
            if (lifecycle.isTerminal() || remainingNanosAtStart <= 0) {
                // Deadline already passed (or another path already terminated) before this worker
                // thread even started — mirrors Phase 1/2's stale-task check
                // (docs/decisions/timeout-semantics.md). Don't touch the response or call Mock LLM.
                log.warn("[{}] skipped: deadline already passed before relay started", requestId);
                return;
            }

            lifecycle.markUpstreamActive();
            metrics.upstreamActiveIncrement();
            HttpURLConnection connection = null;
            try {
                PrintWriter writer = response.getWriter();
                PerStreamWriteChannel writeChannel =
                        new PerStreamWriteChannel(writer, servletWriteExecutor, perStreamBufferCapacity, metrics, lifecycle);
                lifecycle.setWriteChannel(writeChannel);

                long remainingMs = TimeUnit.NANOSECONDS.toMillis(deadlineNanos - System.nanoTime());
                try {
                    connection = mockLlmClient.openStream(requestBody, remainingMs);
                } catch (SocketTimeoutException e) {
                    terminateUnlessStale(lifecycle, "timeout", requestId, "connect_timeout", e);
                    return;
                } catch (IOException e) {
                    terminateUnlessStale(lifecycle, "upstream_error", requestId, "connect_io_error", e);
                    return;
                }
                lifecycle.setConnection(connection);

                BufferedReader reader =
                        new BufferedReader(new InputStreamReader(connection.getInputStream(), StandardCharsets.UTF_8));
                SseFrameReader frameReader = new SseFrameReader(reader);
                lifecycle.markStreaming();

                String frame;
                while ((frame = frameReader.readFrame()) != null) {
                    if (lifecycle.isTerminal()) {
                        // Deadline/disconnect/overflow fired concurrently — stop pulling more
                        // upstream data (the connection is already being torn down by whichever
                        // path won tryTerminate()).
                        return;
                    }
                    boolean accepted = writeChannel.offer(frame);
                    if (!accepted) {
                        // Either already terminal, or offer() itself just triggered write_overflow
                        // termination (PerStreamWriteChannel.offer()'s javadoc) — either way, stop.
                        return;
                    }
                    if (System.nanoTime() > deadlineNanos) {
                        // Per-line deadline recheck (Unit 2 §9 / ADR §4) — defense-in-depth ahead of
                        // the shared watchdog, matching Phase 1/2's relay() loop.
                        lifecycle.tryTerminate("timeout");
                        return;
                    }
                }
                // Natural EOF from Mock LLM. Do NOT call tryTerminate("completed") directly here —
                // frames already offer()'d (in particular the terminal "final" SSE event) may still
                // be sitting in the write channel's buffer, not yet actually written. Signal
                // "no more frames will be offered" instead; the write channel itself calls
                // tryTerminate("completed") only once its buffer has actually drained empty (see
                // PerStreamWriteChannel.markProducerDone()) — this was a real bug found in Smoke A
                // (docs/test-results/phase3/unit2-p3a-functional/), not a hypothetical.
                //
                // The admission permit, however, IS released here — at natural upstream EOF, not at
                // response-drain-complete (Unit 6 admission-semantics unification: docs/decisions/
                // phase3-admission-semantics-unification.md). Downstream (Mock LLM) capacity is
                // freed the moment the upstream call itself is done; gateway_active_streams and the
                // AsyncContext completion still wait for the write channel to actually drain.
                lifecycle.releaseAdmissionPermit();
                writeChannel.markProducerDone();
            } catch (SocketTimeoutException e) {
                // Read-phase timeout — a lower-level safety timeout, not the primary deadline
                // mechanism (docs/decisions/phase3-timeout-cancellation.md §4). Can legitimately
                // race the deadline watchdog; terminateUnlessStale() disambiguates.
                log.info("[{}] blocking read unblocked via SocketTimeoutException at +{}ms (lifecycle.isTerminal()={})",
                        requestId, TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - lifecycle.startNanos()),
                        lifecycle.isTerminal());
                terminateUnlessStale(lifecycle, "timeout", requestId, "read_timeout", e);
            } catch (IOException e) {
                terminateUnlessStale(lifecycle, "upstream_error", requestId, "io_error", e);
            } finally {
                if (connection != null) {
                    connection.disconnect();
                }
            }
        } catch (RuntimeException e) {
            if (lifecycle.isTerminal()) {
                log.warn("[{}] runtime exception after terminal (known race): {}", requestId, e.toString());
            } else {
                log.error("[{}] unexpected error during relay", requestId, e);
                lifecycle.tryTerminate("internal_error");
            }
        } finally {
            metrics.upstreamActiveDecrement();
            metrics.recordBlockingTaskDuration(System.nanoTime() - taskStartNanos);
        }
    }

    private void terminateUnlessStale(RequestLifecycle lifecycle, String outcome, String requestId,
            String reason, IOException e) {
        if (lifecycle.isTerminal()) {
            log.warn("[{}] upstream failure ({}) after already terminal (known race): {}", requestId, reason,
                    e.getMessage());
        } else {
            log.warn("[{}] upstream failure ({}): {}", requestId, reason, e.getMessage());
            lifecycle.tryTerminate(outcome);
        }
    }
}
