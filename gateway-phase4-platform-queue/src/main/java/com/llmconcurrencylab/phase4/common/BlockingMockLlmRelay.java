package com.llmconcurrencylab.phase4.common;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.nio.charset.StandardCharsets;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * Common blocking outbound relay used identically by M1 (Platform ThreadPoolExecutor) and M2
 * (VirtualThreadPerTaskExecutor) — the only difference between M1/M2 is which executor runs
 * {@link #relay} (docs/test-plan/phase4-design.md section 15).
 */
@Component
public class BlockingMockLlmRelay {

    private static final Logger log = LoggerFactory.getLogger(BlockingMockLlmRelay.class);

    private final MockLlmClient mockLlmClient;
    private final GatewayMetrics metrics;

    public BlockingMockLlmRelay(MockLlmClient mockLlmClient, GatewayMetrics metrics) {
        this.mockLlmClient = mockLlmClient;
        this.metrics = metrics;
    }

    /**
     * Runs entirely on the calling (platform or virtual) worker thread. Every outcome is reported
     * through the idempotent {@link RequestLifecycle} CAS — this method never lets an exception
     * escape except genuinely unexpected bugs (caught and mapped to internal_error).
     */
    public void relay(RequestLifecycle lifecycle, String requestBodyJson, PerStreamWriteChannel channel) {
        HttpURLConnection connection = null;
        boolean upstreamStarted = false;
        try {
            connection = mockLlmClient.openStream(requestBodyJson, lifecycle.remainingMs());
            lifecycle.setActiveConnection(connection);
            if (lifecycle.isTerminal()) {
                // deadline/disconnect fired between task pickup and connection open
                connection.disconnect();
                return;
            }
            metrics.upstreamStarted();
            upstreamStarted = true;

            BufferedReader reader = new BufferedReader(
                    new InputStreamReader(connection.getInputStream(), StandardCharsets.UTF_8));
            String line;
            String pendingEvent = null;
            StringBuilder pendingData = new StringBuilder();
            boolean sawFirstEvent = false;
            long startNanos = lifecycle.startNanos();

            while ((line = reader.readLine()) != null) {
                if (lifecycle.isTerminal()) {
                    break;
                }
                if (line.startsWith("event:")) {
                    pendingEvent = line.substring("event:".length()).trim();
                } else if (line.startsWith("data:")) {
                    pendingData.append(line.substring("data:".length()).trim());
                } else if (line.isEmpty()) {
                    if (pendingEvent != null) {
                        if (!sawFirstEvent) {
                            sawFirstEvent = true;
                            metrics.firstUpstreamEvent(System.nanoTime() - startNanos);
                        }
                        SseFrame frame = new SseFrame(pendingEvent, pendingData.toString());
                        boolean accepted = channel.offer(frame);
                        boolean streamEnd = "final".equals(pendingEvent) || "error".equals(pendingEvent);
                        pendingEvent = null;
                        pendingData.setLength(0);
                        if (!accepted || streamEnd) {
                            break;
                        }
                    } else {
                        pendingData.setLength(0);
                    }
                }
                if (lifecycle.isPastDeadline()) {
                    // Bug fix (Unit 5.1): this check races DeadlineWatchdog's own scheduled
                    // callback -- isPastDeadline() can observe true nanoseconds before the
                    // watchdog thread has actually run tryTerminate(TIMEOUT). The old code simply
                    // broke here and fell through to the markProducerDone() call below, which
                    // (since isTerminal() was still false in that race window) misclassified a
                    // deadline-truncated stream as a normal completion once the write channel's
                    // buffer drained -- confirmed via cross-checking three independent observers
                    // (client TTFC/outcome, Mock's own cancelled-request counter, Gateway's own
                    // outcome counter) at M1 N=400 screening (docs/test-results/phase4/
                    // unit5-closed-screening-pre-timeout-fix/m1-n400-screen1/). This relay loop
                    // must contest the terminal CAS itself before breaking -- whichever of {this
                    // call, the watchdog} wins is irrelevant (both produce the same TIMEOUT
                    // outcome exactly once); what matters is that isTerminal() is guaranteed true
                    // by the time control reaches the post-loop check below, so it can never fall
                    // through to markProducerDone() for a stream that was actually abandoned here.
                    if (lifecycle.tryTerminate(Outcome.TIMEOUT)) {
                        channel.terminateNow(Outcome.TIMEOUT);
                    }
                    break;
                }
            }

            if (!lifecycle.isTerminal()) {
                // Upstream EOF/stream-end reached without a terminal outcome already assigned —
                // the write channel itself decides "completed" once its buffer actually drains
                // (docs/decisions/phase3-timeout-cancellation.md section 0 addendum).
                channel.markProducerDone();
            }
        } catch (IOException e) {
            if (lifecycle.tryTerminate(Outcome.UPSTREAM_ERROR)) {
                channel.terminateNow(Outcome.UPSTREAM_ERROR);
            }
            log.info("[{}] upstream relay ended with IOException: {}", lifecycle.requestId(), e.toString());
        } catch (Exception e) {
            if (lifecycle.tryTerminate(Outcome.INTERNAL_ERROR)) {
                channel.terminateNow(Outcome.INTERNAL_ERROR);
            }
            log.error("[{}] unexpected error during relay", lifecycle.requestId(), e);
        } finally {
            if (upstreamStarted) {
                metrics.upstreamEnded();
            }
            if (connection != null) {
                connection.disconnect();
            }
        }
    }
}
