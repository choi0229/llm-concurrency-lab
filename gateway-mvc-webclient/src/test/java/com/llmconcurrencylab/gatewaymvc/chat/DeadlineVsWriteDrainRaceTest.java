package com.llmconcurrencylab.gatewaymvc.chat;

import java.io.PrintWriter;
import java.io.Writer;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

import javax.servlet.AsyncContext;

import com.llmconcurrencylab.gatewaymvc.admission.AdmissionGate;
import com.llmconcurrencylab.gatewaymvc.metrics.GatewayMetrics;
import com.llmconcurrencylab.gatewaymvc.write.PerStreamWriteChannel;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

/**
 * The Unit 3 §24 case explicitly called out as "매우 중요": upstream (WebClient) can complete —
 * i.e. call {@code writeChannel.markProducerDone()} — well before the absolute deadline, but if
 * the Servlet write executor is slow to actually drain the buffered frames (e.g. it's saturated by
 * other streams), the request must still be cut off at the deadline as {@code timeout}, not wait
 * indefinitely and eventually report {@code completed}. Exercises {@link RequestLifecycle} +
 * {@link PerStreamWriteChannel} wired together exactly as {@code ChatController} wires them,
 * without needing a real WebClient/Tomcat — the deadline watchdog is a real
 * {@link ScheduledExecutorService} task, just like production.
 */
class DeadlineVsWriteDrainRaceTest {

    /** A Writer whose write() blocks until manually released — simulates a saturated write executor. */
    static final class BlockableWriter extends Writer {
        private final java.util.concurrent.CountDownLatch releaseLatch = new java.util.concurrent.CountDownLatch(1);

        @Override
        public void write(char[] cbuf, int off, int len) throws java.io.IOException {
            try {
                releaseLatch.await(10, TimeUnit.SECONDS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }

        @Override
        public void flush() {
        }

        @Override
        public void close() {
        }

        void release() {
            releaseLatch.countDown();
        }
    }

    @Test
    void deadlineWinsEvenThoughUpstreamAlreadySignaledProducerDone() throws Exception {
        AsyncContext ctx = mock(AsyncContext.class);
        AdmissionGate gate = new AdmissionGate(5);
        GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
        RequestLifecycle lifecycle = new RequestLifecycle("req-race", ctx, gate, metrics, System.nanoTime());

        BlockableWriter raw = new BlockableWriter();
        PrintWriter writer = new PrintWriter(raw);
        ThreadPoolExecutor writeExecutor = new ThreadPoolExecutor(1, 1, 60, TimeUnit.SECONDS,
                new ArrayBlockingQueue<>(8));
        PerStreamWriteChannel channel = new PerStreamWriteChannel(writer, writeExecutor, 8, metrics, lifecycle);
        lifecycle.setWriteChannel(channel);

        // A short, real deadline — the SAME shared-watchdog mechanism ChatController uses.
        ScheduledExecutorService deadlineExecutor = Executors.newSingleThreadScheduledExecutor();
        ScheduledFuture<?> deadlineTask =
                deadlineExecutor.schedule(() -> lifecycle.tryTerminate("timeout"), 300, TimeUnit.MILLISECONDS);
        lifecycle.setDeadlineTask(deadlineTask);

        // Upstream ("WebClient") completes almost immediately — well before the 300ms deadline —
        // but the frame it offered is now stuck behind a write that will not return until released.
        channel.offer("event: final\ndata: {\"status\":\"COMPLETED\"}\n\n");
        channel.markProducerDone();

        // At this point the write executor's single worker is blocked inside write(), holding the
        // only buffered frame hostage. If the implementation were wrong (e.g. markProducerDone()
        // itself finalized "completed" instead of waiting for the drain), the bug would show here.
        Thread.sleep(600); // past the 300ms deadline

        assertThat(lifecycle.isTerminal()).isTrue();
        assertThat(lifecycle.outcome())
                .as("upstream's early onComplete must not cancel/preempt the deadline watchdog")
                .isEqualTo("timeout");

        raw.release();
        writeExecutor.shutdown();
        deadlineExecutor.shutdown();
        assertThat(writeExecutor.awaitTermination(5, TimeUnit.SECONDS)).isTrue();
    }
}
