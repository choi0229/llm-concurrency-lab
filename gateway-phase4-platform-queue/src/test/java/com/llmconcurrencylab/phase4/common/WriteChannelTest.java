package com.llmconcurrencylab.phase4.common;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.PrintWriter;
import java.io.StringWriter;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;

/**
 * Unit 2 F7 (final-drain) / F8 (write-overflow) — docs/test-plan/phase4-design.md section 6,
 * Unit 2 sections 34-35. Test-only tiny executor/queue, no scalability load. Shared logic between
 * M1/M2 (PerStreamWriteChannel is byte-identical in both modules); duplicated verbatim into M2's
 * test tree for parity.
 */
class WriteChannelTest {

    @Test
    void finalFrameIsNotLostWhenProducerSignalsDoneBeforeDrainCatchesUp() throws Exception {
        ThreadPoolExecutor writeExecutor =
                new ThreadPoolExecutor(1, 1, 0L, TimeUnit.SECONDS, new ArrayBlockingQueue<>(100, false));
        StringWriter sw = new StringWriter();
        PrintWriter writer = new PrintWriter(sw);
        RequestLifecycle lifecycle = new RequestLifecycle("t1", 60000);
        GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
        WriteChannelMetrics writeMetrics = new WriteChannelMetrics(new SimpleMeterRegistry(), writeExecutor);
        AtomicReference<Outcome> finalOutcome = new AtomicReference<>();
        CountDownLatch terminalLatch = new CountDownLatch(1);

        PerStreamWriteChannel channel = new PerStreamWriteChannel(lifecycle, writer, writeExecutor, metrics,
                writeMetrics, outcome -> {
                    finalOutcome.set(outcome);
                    terminalLatch.countDown();
                });

        // Occupy the single write-executor thread so offered frames genuinely queue up before any
        // draining happens (deterministic reproduction of the Phase 3 final-frame-loss race).
        CountDownLatch holdRunning = new CountDownLatch(1);
        CountDownLatch releaseHold = new CountDownLatch(1);
        writeExecutor.execute(() -> {
            holdRunning.countDown();
            try {
                releaseHold.await(5, TimeUnit.SECONDS);
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            }
        });
        assertTrue(holdRunning.await(2, TimeUnit.SECONDS));

        assertTrue(channel.offer(new SseFrame("delta", "1")));
        assertTrue(channel.offer(new SseFrame("delta", "2")));
        assertTrue(channel.offer(new SseFrame("final", "{\"status\":\"COMPLETED\"}")));
        channel.markProducerDone();

        // Nothing written yet -- drain is still blocked behind the hold task.
        assertEquals("", sw.toString());
        assertFalse(terminalLatch.await(200, TimeUnit.MILLISECONDS),
                "must not finalize as completed before the buffer has actually drained");

        releaseHold.countDown();
        assertTrue(terminalLatch.await(2, TimeUnit.SECONDS));
        assertEquals(Outcome.COMPLETED, finalOutcome.get());
        String written = sw.toString();
        assertTrue(written.contains("delta"));
        assertTrue(written.contains("final"));
        assertTrue(written.indexOf("final") > written.lastIndexOf("delta"), "final frame must be written last");

        writeExecutor.shutdown();
        assertTrue(writeExecutor.awaitTermination(5, TimeUnit.SECONDS));
    }

    @Test
    void perStreamBufferOverflowTerminatesAsWriteOverflow() throws Exception {
        ThreadPoolExecutor writeExecutor =
                new ThreadPoolExecutor(1, 1, 0L, TimeUnit.SECONDS, new ArrayBlockingQueue<>(100, false));
        StringWriter sw = new StringWriter();
        PrintWriter writer = new PrintWriter(sw);
        RequestLifecycle lifecycle = new RequestLifecycle("t2", 60000);
        GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
        WriteChannelMetrics writeMetrics = new WriteChannelMetrics(new SimpleMeterRegistry(), writeExecutor);
        AtomicReference<Outcome> finalOutcome = new AtomicReference<>();
        CountDownLatch terminalLatch = new CountDownLatch(1);

        PerStreamWriteChannel channel = new PerStreamWriteChannel(lifecycle, writer, writeExecutor, metrics,
                writeMetrics, outcome -> {
                    finalOutcome.set(outcome);
                    terminalLatch.countDown();
                });

        CountDownLatch holdRunning = new CountDownLatch(1);
        CountDownLatch releaseHold = new CountDownLatch(1);
        writeExecutor.execute(() -> {
            holdRunning.countDown();
            try {
                releaseHold.await(5, TimeUnit.SECONDS);
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            }
        });
        assertTrue(holdRunning.await(2, TimeUnit.SECONDS));

        // PER_STREAM_CAPACITY defaults to 8 (docs/test-plan/phase4-design.md section 0-2) when
        // SERVLET_PER_STREAM_BUFFER_CAPACITY is unset, which is the case for this test JVM.
        for (int i = 0; i < 8; i++) {
            assertTrue(channel.offer(new SseFrame("delta", "frame-" + i)), "frame " + i + " should fit in capacity 8");
        }
        boolean ninthAccepted = channel.offer(new SseFrame("delta", "overflow-frame"));
        assertFalse(ninthAccepted, "9th frame must overflow a capacity-8 buffer that nothing has drained yet");

        assertTrue(terminalLatch.await(2, TimeUnit.SECONDS));
        assertEquals(Outcome.WRITE_OVERFLOW, finalOutcome.get());

        releaseHold.countDown();
        writeExecutor.shutdown();
        assertTrue(writeExecutor.awaitTermination(5, TimeUnit.SECONDS));
    }
}
