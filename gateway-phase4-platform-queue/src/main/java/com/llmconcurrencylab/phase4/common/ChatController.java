package com.llmconcurrencylab.phase4.common;

import java.io.IOException;
import java.io.PrintWriter;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.function.Consumer;

import jakarta.servlet.AsyncContext;
import jakarta.servlet.AsyncEvent;
import jakarta.servlet.AsyncListener;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;

/**
 * Common M1/M2 controller (docs/test-plan/phase4-design.md section 3, 8, 9). Byte-identical
 * between the two modules — the only per-model wiring is which {@link ChatTaskSubmitter}
 * implementation Spring injects (Platform vs Virtual). Request lifecycle start = request body
 * decode success (here: the moment Spring MVC has bound the {@code @RequestBody String}, i.e. at
 * method entry — docs/test-plan/phase4-design.md section 9).
 */
@RestController
@RequestMapping("/chat")
public class ChatController {

    private static final Logger log = LoggerFactory.getLogger(ChatController.class);

    /** AsyncContext container-level timeout = app deadline + this margin (safety backstop only —
     * docs/decisions/phase3-timeout-cancellation.md section 5). Should essentially never fire. */
    private static final int SAFETY_MARGIN_MS = 5000;

    private final ChatTaskSubmitter taskSubmitter;
    private final BlockingMockLlmRelay relay;
    private final GatewayMetrics metrics;
    private final WriteChannelMetrics writeMetrics;
    private final ThreadPoolExecutor writeExecutor;
    private final DeadlineWatchdog watchdog;
    private final RequestIdGenerator requestIdGenerator;
    private final int totalTimeoutMs;

    public ChatController(ChatTaskSubmitter taskSubmitter, BlockingMockLlmRelay relay, GatewayMetrics metrics,
            WriteChannelMetrics writeMetrics, ThreadPoolExecutor writeExecutor, DeadlineWatchdog watchdog,
            RequestIdGenerator requestIdGenerator) {
        this.taskSubmitter = taskSubmitter;
        this.relay = relay;
        this.metrics = metrics;
        this.writeMetrics = writeMetrics;
        this.writeExecutor = writeExecutor;
        this.watchdog = watchdog;
        this.requestIdGenerator = requestIdGenerator;
        this.totalTimeoutMs = EnvUtil.getInt("CHAT_TOTAL_TIMEOUT_MS", 60000);
    }

    @RequestMapping(value = "/stream", method = RequestMethod.POST)
    public void stream(HttpServletRequest request, @RequestBody(required = false) String body) {
        String requestId = requestIdGenerator.next();
        RequestLifecycle lifecycle = new RequestLifecycle(requestId, totalTimeoutMs);
        metrics.requestStarted();
        String requestBody = (body == null || body.isEmpty()) ? "{}" : body;

        AsyncContext asyncContext = request.startAsync();
        asyncContext.setTimeout(totalTimeoutMs + SAFETY_MARGIN_MS);
        HttpServletResponse response = (HttpServletResponse) asyncContext.getResponse();
        response.setContentType("text/event-stream");
        response.setCharacterEncoding("UTF-8");

        asyncContext.addListener(new AsyncListener() {
            @Override
            public void onComplete(AsyncEvent event) {
                if (lifecycle.tryTerminate(Outcome.INTERNAL_ERROR)) {
                    log.warn("[{}] onComplete won the terminal race (unexpected path)", requestId);
                    finalizeOutcome(lifecycle, Outcome.INTERNAL_ERROR, asyncContext, true);
                }
            }

            @Override
            public void onTimeout(AsyncEvent event) {
                if (lifecycle.tryTerminate(Outcome.TIMEOUT)) {
                    log.warn("[{}] AsyncContext safety watchdog fired (app-level deadline did not)", requestId);
                    lifecycle.disconnectActiveConnectionIfAny();
                    finalizeOutcome(lifecycle, Outcome.TIMEOUT, asyncContext, false);
                }
            }

            @Override
            public void onError(AsyncEvent event) {
                if (lifecycle.tryTerminate(Outcome.CLIENT_DISCONNECT)) {
                    finalizeOutcome(lifecycle, Outcome.CLIENT_DISCONNECT, asyncContext, false);
                }
            }

            @Override
            public void onStartAsync(AsyncEvent event) {
            }
        });

        PrintWriter writer;
        try {
            writer = response.getWriter();
        } catch (IOException e) {
            if (lifecycle.tryTerminate(Outcome.INTERNAL_ERROR)) {
                finalizeOutcome(lifecycle, Outcome.INTERNAL_ERROR, asyncContext, false);
            }
            return;
        }

        Consumer<Outcome> onTerminal = outcome -> finalizeOutcome(lifecycle, outcome, asyncContext, false);
        PerStreamWriteChannel channel =
                new PerStreamWriteChannel(lifecycle, writer, writeExecutor, metrics, writeMetrics, onTerminal);

        watchdog.arm(lifecycle, lc -> {
            lc.disconnectActiveConnectionIfAny();
            channel.terminateNow(Outcome.TIMEOUT);
        });

        Runnable task = () -> {
            if (lifecycle.isTerminal()) {
                // Queued/dispatched after this request already reached a terminal outcome
                // (deadline/disconnect) — must not start upstream (no phantom upstream call,
                // docs/test-plan/phase4-design.md section 11 / Unit 2 section 11).
                return;
            }
            relay.relay(lifecycle, requestBody, channel);
        };

        boolean accepted = taskSubmitter.trySubmit(lifecycle, task);
        if (!accepted && lifecycle.tryTerminate(Outcome.REJECTED)) {
            response.setStatus(503);
            finalizeOutcome(lifecycle, Outcome.REJECTED, asyncContext, false);
        }
    }

    private void finalizeOutcome(RequestLifecycle lifecycle, Outcome outcome, AsyncContext asyncContext,
            boolean skipComplete) {
        lifecycle.cancelDeadlineFuture();
        metrics.requestTerminal(outcome);
        metrics.requestDuration(System.nanoTime() - lifecycle.startNanos());
        if (!skipComplete) {
            try {
                asyncContext.complete();
            } catch (IllegalStateException e) {
                log.debug("[{}] asyncContext.complete() raced with container completion: {}",
                        lifecycle.requestId(), e.toString());
            }
        }
    }
}
