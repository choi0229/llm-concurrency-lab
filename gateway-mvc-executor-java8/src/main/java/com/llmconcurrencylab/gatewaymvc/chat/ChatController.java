package com.llmconcurrencylab.gatewaymvc.chat;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.PrintWriter;
import java.net.HttpURLConnection;
import java.net.SocketTimeoutException;
import java.nio.charset.StandardCharsets;
import java.util.UUID;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

import javax.servlet.AsyncContext;
import javax.servlet.AsyncEvent;
import javax.servlet.AsyncListener;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

import com.llmconcurrencylab.gatewaymvc.config.EnvUtil;
import com.llmconcurrencylab.gatewaymvc.executor.InstrumentedRunnable;
import com.llmconcurrencylab.gatewaymvc.metrics.GatewayMetrics;
import com.llmconcurrencylab.gatewaymvc.upstream.MockLlmClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;

/**
 * Baseline relay: Front -> Servlet AsyncContext -> chat ThreadPoolExecutor ->
 * HttpURLConnection (blocking) -> Mock LLM SSE -> relayed back to Front. The
 * Tomcat request thread returns immediately after startAsync(); the chat
 * executor thread blocks for the entire upstream call, which is exactly the
 * structure this Baseline exists to measure.
 */
@RestController
@RequestMapping("/chat")
public class ChatController {

    private static final Logger log = LoggerFactory.getLogger(ChatController.class);

    private final ThreadPoolExecutor chatExecutor;
    private final MockLlmClient mockLlmClient;
    private final GatewayMetrics metrics;
    private final int totalTimeoutMs;

    @Autowired
    public ChatController(ThreadPoolExecutor chatExecutor, MockLlmClient mockLlmClient, GatewayMetrics metrics) {
        this.chatExecutor = chatExecutor;
        this.mockLlmClient = mockLlmClient;
        this.metrics = metrics;
        this.totalTimeoutMs = EnvUtil.getInt("CHAT_TOTAL_TIMEOUT_MS", 60000);
    }

    @RequestMapping(value = "/stream", method = RequestMethod.POST)
    public void stream(HttpServletRequest request, @RequestBody(required = false) String body) throws IOException {
        // Captured before executor submission so gateway_ttfb_seconds reflects
        // the client's actual wait (queue wait + upstream firstChunkDelay), not
        // just the upstream call once a worker thread finally picks it up. This
        // is what makes it comparable to client_ttfc_seconds (see H1-c).
        final long requestReceivedNanos = System.nanoTime();
        metrics.requestReceivedTotal.inc();
        final String requestBody = (body == null || body.isEmpty()) ? "{}" : body;
        final String requestId = UUID.randomUUID().toString();

        final AsyncContext asyncContext = request.startAsync();
        asyncContext.setTimeout(totalTimeoutMs);
        HttpServletResponse response = (HttpServletResponse) asyncContext.getResponse();
        response.setContentType("text/event-stream");
        response.setCharacterEncoding("UTF-8");

        final AsyncRequestState state = new AsyncRequestState();
        metrics.asyncActiveRequests.inc();
        asyncContext.addListener(new AsyncListener() {
            @Override
            public void onComplete(AsyncEvent event) {
                // Normally a no-op win: relay()'s finally (or the rejected-path
                // handler) already calls releaseOnce with the real outcome
                // before ever calling asyncContext.complete(), so by the time
                // this fires the CAS has already lost. If this DOES win the
                // race, it means the container completed the AsyncContext
                // through some path our own code never observed — genuinely
                // unexpected, so it is not silently folded into any of the
                // "normal" outcomes.
                state.releaseOnce("unexpected_error", new Runnable() {
                    public void run() {
                        log.warn("[{}] onComplete won the release race (unexpected path)", requestId);
                        metrics.asyncActiveRequests.dec();
                        metrics.requestOutcomeTotal.labels("unexpected_error").inc();
                    }
                });
            }

            @Override
            public void onTimeout(AsyncEvent event) {
                metrics.asyncTimeoutTotal.inc();
                log.warn("[{}] async timeout after {}ms", requestId, totalTimeoutMs);
                // If this wins the release race, the worker never got far
                // enough into relay() to record its own outcome — i.e. the
                // task was still queued, or its own deadline check hadn't run
                // yet. Either way, from the request's point of view nothing
                // useful happened before the deadline.
                state.releaseOnce("timeout_before_start", new Runnable() {
                    public void run() {
                        metrics.asyncActiveRequests.dec();
                        metrics.requestOutcomeTotal.labels("timeout_before_start").inc();
                    }
                });
                event.getAsyncContext().complete();
            }

            @Override
            public void onError(AsyncEvent event) {
                Throwable t = event.getThrowable();
                log.warn("[{}] async error: {}", requestId, t != null ? t.getMessage() : "unknown");
                // Servlet onError predominantly fires for the same class of
                // problem PrintWriter.checkError() detects in relay() (a
                // broken client connection) but via a different signal path
                // that can race it — treated as the same outcome bucket.
                state.releaseOnce("client_disconnect", new Runnable() {
                    public void run() {
                        metrics.asyncActiveRequests.dec();
                        metrics.requestOutcomeTotal.labels("client_disconnect").inc();
                    }
                });
            }

            @Override
            public void onStartAsync(AsyncEvent event) {
            }
        });

        final long absoluteDeadlineNanos = requestReceivedNanos + TimeUnit.MILLISECONDS.toNanos(totalTimeoutMs);
        Runnable work = new Runnable() {
            @Override
            public void run() {
                relay(asyncContext, requestBody, requestId, state, requestReceivedNanos, absoluteDeadlineNanos);
            }
        };

        try {
            chatExecutor.execute(new InstrumentedRunnable(work, metrics, state::setQueueWaitSeconds));
        } catch (RejectedExecutionException e) {
            metrics.executorRejectedTotal.inc();
            log.warn("[{}] rejected: chat executor saturated", requestId);
            try {
                response.setStatus(HttpServletResponse.SC_SERVICE_UNAVAILABLE);
                response.getWriter().write("{\"status\":\"REJECTED\",\"reason\":\"executor_saturated\"}");
            } catch (IOException ignored) {
                // client already gone; nothing more to do
            } finally {
                // Rejected pre-accept: chatExecutor.execute() never queued
                // this task, so it is excluded from "accepted" by definition
                // (docs/decisions/request-outcome-accounting.md) even though
                // gateway_async_active_requests was already incremented above.
                boolean weReleasedIt = state.releaseOnce("rejected", new Runnable() {
                    public void run() {
                        metrics.asyncActiveRequests.dec();
                        metrics.requestOutcomeTotal.labels("rejected").inc();
                    }
                });
                if (weReleasedIt) {
                    asyncContext.complete();
                }
            }
        }
    }

    private void relay(AsyncContext asyncContext, String requestBody, String requestId, AsyncRequestState state,
            long requestReceivedNanos, long absoluteDeadlineNanos) {
        // Fine-grained authoritative outcome — one of the fixed
        // gateway_request_outcome_total label values (docs/decisions/request-outcome-accounting.md).
        // Only the value actually recorded by the winning releaseOnce() call
        // (in `finally`, below) ever reaches the metric; every early return
        // sets this first so `finally` doesn't have to re-derive it.
        String outcome = "unexpected_error";
        long remainingNanosAtStart = absoluteDeadlineNanos - System.nanoTime();

        // Primary defense (docs/decisions/timeout-semantics.md): if the
        // AsyncContext is already finalized, OR the single absolute deadline
        // has already passed, don't touch `response` or call Mock LLM at all.
        if (state.isReleased() || remainingNanosAtStart <= 0) {
            metrics.staleTaskSkippedTotal.inc();
            log.warn("[{}] skipped: deadline already passed before this task started (remaining={}ms)",
                    requestId, TimeUnit.NANOSECONDS.toMillis(remainingNanosAtStart));
            // If state.isReleased() is already true, onTimeout already won the
            // release race and recorded "timeout_before_start" itself — the
            // recordQueueWait/releaseOnce calls below are then both no-ops.
            // If it's false (our own clock tripped a hair before the
            // container's), we leave the AsyncContext alone; the container's
            // own onTimeout will still fire shortly and do the release.
            recordQueueWait(state, "timeout_before_start");
            return;
        }

        HttpServletResponse response = (HttpServletResponse) asyncContext.getResponse();
        HttpURLConnection connection = null;
        try {
            PrintWriter writer = response.getWriter();
            long remainingMs = TimeUnit.NANOSECONDS.toMillis(remainingNanosAtStart);
            try {
                connection = mockLlmClient.openStream(requestBody, remainingMs);
            } catch (SocketTimeoutException e) {
                outcome = recordUpstreamFailure("connect_timeout", "upstream_timeout", requestId, e);
                return;
            } catch (IOException e) {
                outcome = recordUpstreamFailure("io_error", "upstream_error", requestId, e);
                return;
            }

            BufferedReader reader = new BufferedReader(
                    new InputStreamReader(connection.getInputStream(), StandardCharsets.UTF_8));
            boolean firstChunk = true;
            String line;
            while ((line = reader.readLine()) != null) {
                if (firstChunk) {
                    metrics.ttfbSeconds.observe((System.nanoTime() - requestReceivedNanos) / 1_000_000_000.0);
                    firstChunk = false;
                }
                writer.write(line);
                writer.write("\n");
                if (line.isEmpty()) {
                    // SSE event boundary: flush what we have so the client sees it now.
                    writer.flush();
                }
                if (writer.checkError()) {
                    // PrintWriter swallows IOExceptions; checkError() is the only signal.
                    metrics.clientDisconnectTotal.inc();
                    log.info("[{}] client disconnected mid-stream", requestId);
                    outcome = "client_disconnect";
                    return;
                }
                if (System.nanoTime() > absoluteDeadlineNanos) {
                    metrics.relayDeadlineExceededTotal.inc();
                    log.warn("[{}] total timeout ({}ms) exceeded, closing upstream", requestId, totalTimeoutMs);
                    outcome = "deadline_exceeded";
                    return;
                }
            }
            // Reached only via natural EOF from Mock LLM.
            outcome = "completed";
        } catch (SocketTimeoutException e) {
            // Read-phase timeout: getInputStream()/readLine() blocked past the
            // clamped read timeout. See docs/decisions/timeout-semantics.md
            // ("Absolute Deadline Blocking Read") — this can legitimately fire
            // AFTER the AsyncContext has already timed out, since
            // HttpURLConnection's read timeout resets per-read rather than
            // tracking the absolute deadline; recordUpstreamFailure's diagnostic
            // counter still increments in that case, but releaseOnce below will
            // be a no-op if onTimeout already won.
            outcome = recordUpstreamFailure("read_timeout", "upstream_timeout", requestId, e);
        } catch (IOException e) {
            // Any other read-phase IOException — not a client disconnect (the
            // write path never throws; see checkError() above), so this is
            // always an upstream-side failure.
            outcome = recordUpstreamFailure("io_error", "upstream_error", requestId, e);
        } catch (RuntimeException e) {
            // Defensive net, NOT the primary mechanism (see
            // docs/decisions/timeout-semantics.md): the checks above catch the
            // common case, but onTimeout can in principle fire concurrently
            // while this thread is already mid-relay. When that happens,
            // Tomcat's recycled Request/Response throws whatever it throws —
            // observed in practice to be NullPointerException deep in
            // Http11OutputBuffer.commit(), NOT the IllegalStateException one
            // might expect — so this catches RuntimeException broadly rather
            // than guessing the exact type. state.isReleased() disambiguates
            // the known race from a genuine bug so the two are never counted
            // under the same metric.
            if (state.isReleased()) {
                metrics.staleTaskSkippedTotal.inc();
                log.warn("[{}] AsyncContext became invalid mid-relay (known race): {}", requestId, e.toString());
                outcome = "deadline_exceeded";
            } else {
                metrics.unexpectedRuntimeErrorTotal.inc();
                log.error("[{}] unexpected error during relay (state was NOT released — investigate)", requestId, e);
                outcome = "unexpected_error";
            }
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
            final String finalOutcome = outcome;
            recordQueueWait(state, finalOutcome);
            // Only complete() if this call actually performed the release —
            // if an AsyncListener callback already did (e.g. a concurrent
            // onTimeout), the AsyncContext is already completed and calling
            // complete() again throws IllegalStateException. Either way,
            // gateway_request_outcome_total only moves on the winning call,
            // which is what keeps it exactly-once
            // (docs/decisions/request-outcome-accounting.md).
            boolean weReleasedIt = state.releaseOnce(finalOutcome, new Runnable() {
                public void run() {
                    metrics.asyncActiveRequests.dec();
                    metrics.requestOutcomeTotal.labels(finalOutcome).inc();
                }
            });
            log.info("[{}] relay finished: outcome={}, releasedByThisCall={}", requestId, finalOutcome, weReleasedIt);
            if (weReleasedIt) {
                asyncContext.complete();
            }
        }
    }

    /**
     * Increments the diagnostic gateway_upstream_failure_total{reason} counter
     * (allowed to fire even when this code path doesn't end up "winning" the
     * request's terminal outcome — see relay()'s SocketTimeoutException catch
     * comment) and returns the authoritative outcome value the caller should
     * assign to its local `outcome` variable.
     */
    private String recordUpstreamFailure(String reason, String outcome, String requestId, IOException e) {
        metrics.upstreamFailureTotal.labels(reason).inc();
        log.warn("[{}] upstream failure ({}): {}", requestId, reason, e.getMessage());
        return outcome;
    }

    /**
     * Binary bucket for executor_queue_wait_seconds{outcome=completed|failed}
     * — deliberately coarser than the fine-grained authoritative outcome
     * above (see GatewayMetrics.executorQueueWaitSeconds and Unit 5 section 6
     * result table, which only ever needs completed vs failed queue wait).
     */
    private void recordQueueWait(AsyncRequestState state, String fineOutcome) {
        Double queueWaitSeconds = state.getQueueWaitSeconds();
        if (queueWaitSeconds != null) {
            String bucket = "completed".equals(fineOutcome) ? "completed" : "failed";
            metrics.executorQueueWaitSeconds.labels(bucket).observe(queueWaitSeconds);
        }
    }
}
