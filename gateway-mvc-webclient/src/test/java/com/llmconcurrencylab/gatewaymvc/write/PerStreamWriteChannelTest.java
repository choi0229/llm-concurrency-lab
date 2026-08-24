package com.llmconcurrencylab.gatewaymvc.write;

import java.io.IOException;
import java.io.PrintWriter;
import java.io.Writer;
import java.util.List;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.stream.IntStream;

import javax.servlet.AsyncContext;

import com.llmconcurrencylab.gatewaymvc.admission.AdmissionGate;
import com.llmconcurrencylab.gatewaymvc.chat.RequestLifecycle;
import com.llmconcurrencylab.gatewaymvc.metrics.GatewayMetrics;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

/**
 * Exercises the serialized-writer contract from docs/decisions/phase3-mvc-webclient-write-path.md
 * §4 and Unit 2 §12/§13/§21 directly against {@link PerStreamWriteChannel}, without going through
 * a Servlet container.
 */
class PerStreamWriteChannelTest {

    /**
     * A Writer whose write()/flush() can be told to throw IOException (simulating a broken client
     * connection so PrintWriter.checkError() becomes true, ADR §6/Unit 2 §15) and whose write()
     * calls track concurrent entrancy so a violation of "no concurrent PrintWriter.write()" fails
     * the test instead of silently interleaving.
     */
    static final class RecordingWriter extends Writer {
        final List<String> written = new CopyOnWriteArrayList<>();
        private final AtomicInteger concurrentEntrants = new AtomicInteger(0);
        private volatile boolean throwOnNextWrite = false;
        volatile boolean concurrencyViolationObserved = false;
        private final long perWriteDelayNanos;

        RecordingWriter() {
            this(0);
        }

        RecordingWriter(long perWriteDelayNanos) {
            this.perWriteDelayNanos = perWriteDelayNanos;
        }

        @Override
        public void write(char[] cbuf, int off, int len) throws IOException {
            if (throwOnNextWrite) {
                throw new IOException("simulated broken connection");
            }
            int entrants = concurrentEntrants.incrementAndGet();
            if (entrants > 1) {
                concurrencyViolationObserved = true;
            }
            try {
                if (perWriteDelayNanos > 0) {
                    long deadline = System.nanoTime() + perWriteDelayNanos;
                    while (System.nanoTime() < deadline) {
                        // busy-wait to widen the concurrency window deterministically
                    }
                }
                written.add(new String(cbuf, off, len));
            } finally {
                concurrentEntrants.decrementAndGet();
            }
        }

        @Override
        public void flush() {
        }

        @Override
        public void close() {
        }

        void breakConnection() {
            throwOnNextWrite = true;
        }
    }

    private RequestLifecycle newLifecycle(GatewayMetrics metrics) {
        AsyncContext ctx = mock(AsyncContext.class);
        AdmissionGate gate = new AdmissionGate(5);
        return new RequestLifecycle("req-1", ctx, gate, metrics, System.nanoTime());
    }

    private ThreadPoolExecutor newWriteExecutor(int poolSize, int queueCapacity) {
        return new ThreadPoolExecutor(poolSize, poolSize, 60, TimeUnit.SECONDS,
                new ArrayBlockingQueue<>(queueCapacity));
    }

    @Test
    void framesAreWrittenInOfferOrder() throws Exception {
        RecordingWriter raw = new RecordingWriter();
        PrintWriter writer = new PrintWriter(raw);
        GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
        RequestLifecycle lifecycle = newLifecycle(metrics);
        ThreadPoolExecutor exec = newWriteExecutor(2, 16);
        PerStreamWriteChannel channel = new PerStreamWriteChannel(writer, exec, 16, metrics, lifecycle);

        for (int i = 0; i < 10; i++) {
            assertThat(channel.offer("frame-" + i + "\n")).isTrue();
        }
        waitUntil(() -> raw.written.size() == 10);

        List<String> expected = IntStream.range(0, 10).mapToObj(i -> "frame-" + i + "\n").collect(java.util.stream.Collectors.toList());
        assertThat(raw.written).containsExactlyElementsOf(expected);
        exec.shutdownNow();
    }

    @Test
    void concurrentProducersNeverCauseConcurrentWrite() throws Exception {
        RecordingWriter raw = new RecordingWriter(TimeUnit.MICROSECONDS.toNanos(200)); // widen the race window
        PrintWriter writer = new PrintWriter(raw);
        GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
        RequestLifecycle lifecycle = newLifecycle(metrics);
        ThreadPoolExecutor exec = newWriteExecutor(4, 256);
        PerStreamWriteChannel channel = new PerStreamWriteChannel(writer, exec, 256, metrics, lifecycle);

        int producers = 8;
        int framesPerProducer = 25;
        ExecutorService producerPool = Executors.newFixedThreadPool(producers);
        CountDownLatch start = new CountDownLatch(1);
        CountDownLatch done = new CountDownLatch(producers);
        for (int p = 0; p < producers; p++) {
            producerPool.submit(() -> {
                try {
                    start.await();
                    for (int i = 0; i < framesPerProducer; i++) {
                        channel.offer("x\n");
                    }
                } catch (InterruptedException ignored) {
                } finally {
                    done.countDown();
                }
            });
        }
        start.countDown();
        assertThat(done.await(10, TimeUnit.SECONDS)).isTrue();
        producerPool.shutdown();

        waitUntil(() -> raw.written.size() == producers * framesPerProducer);
        assertThat(raw.concurrencyViolationObserved)
                .as("PrintWriter.write() must never be entered concurrently for the same stream")
                .isFalse();
        exec.shutdownNow();
    }

    @Test
    void bufferOverflowTerminatesTheStreamAndDiscardsPending() throws Exception {
        RecordingWriter raw = new RecordingWriter();
        PrintWriter writer = new PrintWriter(raw);
        SimpleMeterRegistry registry = new SimpleMeterRegistry();
        GatewayMetrics metrics = new GatewayMetrics(registry);
        RequestLifecycle lifecycle = newLifecycle(metrics);
        // A single-thread, zero-queue executor that we starve by holding its one worker busy, so
        // frames pile up in the channel's own bounded buffer instead of draining immediately.
        ThreadPoolExecutor exec = newWriteExecutor(1, 1);
        CountDownLatch blockWorker = new CountDownLatch(1);
        exec.execute(() -> {
            try {
                blockWorker.await();
            } catch (InterruptedException ignored) {
            }
        });

        int capacity = 4;
        PerStreamWriteChannel channel = new PerStreamWriteChannel(writer, exec, capacity, metrics, lifecycle);
        for (int i = 0; i < capacity; i++) {
            assertThat(channel.offer("f" + i + "\n")).isTrue();
        }
        // capacity reached (drain task itself is queued behind the blocked worker, so nothing has
        // drained yet) — the next offer must overflow.
        assertThat(channel.offer("overflow\n")).isFalse();

        assertThat(lifecycle.isTerminal()).isTrue();
        assertThat(lifecycle.outcome()).isEqualTo("write_overflow");
        assertThat(registry.get("gateway.write.overflow").counter().count()).isEqualTo(1.0);
        // Unit 5.5 D2 regression: a real slow-client run found this gauge left permanently
        // overcounted after overflow (the discard branch cleared the buffer without decrementing
        // it) — postflight invariant requires this back at 0 once the stream is terminal.
        assertThat(metrics.streamBufferedFramesValue()).isZero();

        blockWorker.countDown();
        exec.shutdown();
        assertThat(exec.awaitTermination(5, TimeUnit.SECONDS)).isTrue();
    }

    @Test
    void enqueueAfterCompletionIsRejected() throws Exception {
        RecordingWriter raw = new RecordingWriter();
        PrintWriter writer = new PrintWriter(raw);
        GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
        RequestLifecycle lifecycle = newLifecycle(metrics);
        ThreadPoolExecutor exec = newWriteExecutor(2, 16);
        PerStreamWriteChannel channel = new PerStreamWriteChannel(writer, exec, 16, metrics, lifecycle);
        lifecycle.setWriteChannel(channel);

        assertThat(channel.offer("before\n")).isTrue();
        lifecycle.tryTerminate("completed");

        assertThat(channel.offer("after\n")).isFalse();
        exec.shutdownNow();
    }

    @Test
    void enqueueAfterTimeoutIsRejected() throws Exception {
        RecordingWriter raw = new RecordingWriter();
        PrintWriter writer = new PrintWriter(raw);
        GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
        RequestLifecycle lifecycle = newLifecycle(metrics);
        ThreadPoolExecutor exec = newWriteExecutor(2, 16);
        PerStreamWriteChannel channel = new PerStreamWriteChannel(writer, exec, 16, metrics, lifecycle);
        lifecycle.setWriteChannel(channel);

        lifecycle.tryTerminate("timeout");
        assertThat(channel.offer("too-late\n")).isFalse();
        exec.shutdownNow();
    }

    @Test
    void disconnectDiscardsPendingFrames() throws Exception {
        RecordingWriter raw = new RecordingWriter();
        PrintWriter writer = new PrintWriter(raw);
        GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
        RequestLifecycle lifecycle = newLifecycle(metrics);
        ThreadPoolExecutor exec = newWriteExecutor(1, 1);
        CountDownLatch blockWorker = new CountDownLatch(1);
        exec.execute(() -> {
            try {
                blockWorker.await();
            } catch (InterruptedException ignored) {
            }
        });

        PerStreamWriteChannel channel = new PerStreamWriteChannel(writer, exec, 8, metrics, lifecycle);
        lifecycle.setWriteChannel(channel);
        channel.offer("pending-1\n");
        channel.offer("pending-2\n");

        lifecycle.tryTerminate("client_disconnect");
        blockWorker.countDown();
        exec.shutdown();
        assertThat(exec.awaitTermination(5, TimeUnit.SECONDS)).isTrue();

        // The blocked worker only ever ran the placeholder task; the drain task for pending-1/-2
        // was never submitted (draining flag guarded it), and markTerminal() discarded the buffer.
        assertThat(raw.written).isEmpty();
    }

    @Test
    void checkErrorDuringWriteTriggersClientDisconnectAndDiscardsRemaining() throws Exception {
        RecordingWriter raw = new RecordingWriter();
        PrintWriter writer = new PrintWriter(raw);
        GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
        RequestLifecycle lifecycle = newLifecycle(metrics);
        ThreadPoolExecutor exec = newWriteExecutor(1, 8);
        PerStreamWriteChannel channel = new PerStreamWriteChannel(writer, exec, 8, metrics, lifecycle);
        lifecycle.setWriteChannel(channel);

        raw.breakConnection();
        channel.offer("first\n"); // this write throws inside PrintWriter, checkError() becomes true
        waitUntil(lifecycle::isTerminal);

        assertThat(lifecycle.outcome()).isEqualTo("client_disconnect");
        exec.shutdownNow();
    }

    @Test
    void writeExecutorRejectionCleansUpAndDoesNotStrandFramesOrDrainingFlag() throws Exception {
        RecordingWriter raw = new RecordingWriter();
        PrintWriter writer = new PrintWriter(raw);
        SimpleMeterRegistry registry = new SimpleMeterRegistry();
        GatewayMetrics metrics = new GatewayMetrics(registry);
        RequestLifecycle lifecycle = newLifecycle(metrics);
        ExecutorService exec = Executors.newSingleThreadExecutor();
        exec.shutdown(); // guarantees every execute() call throws RejectedExecutionException

        PerStreamWriteChannel channel = new PerStreamWriteChannel(writer, exec, 8, metrics, lifecycle);
        lifecycle.setWriteChannel(channel);

        assertThat(channel.offer("x\n")).isFalse();
        assertThat(lifecycle.isTerminal()).isTrue();
        assertThat(lifecycle.outcome()).isEqualTo("internal_error");
        assertThat(registry.get("servlet.write.executor.rejected").counter().count()).isEqualTo(1.0);
        // A later offer must also be rejected — not left in limbo.
        assertThat(channel.offer("y\n")).isFalse();
    }

    @Test
    void drainRecoversFromEmptyBufferAndResumesForLaterFrames() throws Exception {
        RecordingWriter raw = new RecordingWriter();
        PrintWriter writer = new PrintWriter(raw);
        GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
        RequestLifecycle lifecycle = newLifecycle(metrics);
        ThreadPoolExecutor exec = newWriteExecutor(2, 16);
        PerStreamWriteChannel channel = new PerStreamWriteChannel(writer, exec, 16, metrics, lifecycle);

        channel.offer("a\n");
        waitUntil(() -> raw.written.size() == 1); // draining flag should now be back to false

        channel.offer("b\n");
        channel.offer("c\n");
        waitUntil(() -> raw.written.size() == 3);

        assertThat(raw.written).containsExactly("a\n", "b\n", "c\n");
        exec.shutdownNow();
    }

    /**
     * Regression test for a real bug found in Smoke A (docs/test-results/phase3/
     * unit2-p3a-functional/): the "final" SSE event is offer()'d right before natural EOF, then
     * ChatController.relay() used to call lifecycle.tryTerminate("completed") immediately — whose
     * cleanup (markTerminal()) discarded the buffer before the write executor had actually drained
     * it, so the client never received the last frame even though the outcome was "completed".
     * markProducerDone() must defer the "completed" transition until the buffer is actually empty.
     */
    @Test
    void completionWaitsForPendingFramesToDrainBeforeTerminating() throws Exception {
        GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
        RequestLifecycle lifecycle = newLifecycle(metrics);
        // Single slow worker widens the window between "frame offered" and "frame actually written"
        // so a regression (finalizing before drain) would reliably reproduce, not just sometimes.
        RecordingWriter slowRaw = new RecordingWriter(TimeUnit.MILLISECONDS.toNanos(50));
        PrintWriter slowWriter = new PrintWriter(slowRaw);
        ThreadPoolExecutor exec = newWriteExecutor(1, 16);
        PerStreamWriteChannel channel = new PerStreamWriteChannel(slowWriter, exec, 16, metrics, lifecycle);
        lifecycle.setWriteChannel(channel);

        assertThat(channel.offer("event: delta\ndata: {\"sequence\":1}\n\n")).isTrue();
        assertThat(channel.offer("event: final\ndata: {\"status\":\"COMPLETED\"}\n\n")).isTrue();
        channel.markProducerDone(); // relay() reached natural EOF right after offering the final frame

        // Enqueue-after-producer-done must be rejected, same as enqueue-after-completion.
        assertThat(channel.offer("too-late\n")).isFalse();

        waitUntil(lifecycle::isTerminal);
        assertThat(lifecycle.outcome()).isEqualTo("completed");
        assertThat(slowRaw.written).containsExactly(
                "event: delta\ndata: {\"sequence\":1}\n\n",
                "event: final\ndata: {\"status\":\"COMPLETED\"}\n\n");
        exec.shutdownNow();
    }

    @Test
    void producerDoneWithEmptyBufferFinalizesImmediately() throws Exception {
        RecordingWriter raw = new RecordingWriter();
        PrintWriter writer = new PrintWriter(raw);
        GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
        RequestLifecycle lifecycle = newLifecycle(metrics);
        ThreadPoolExecutor exec = newWriteExecutor(2, 16);
        PerStreamWriteChannel channel = new PerStreamWriteChannel(writer, exec, 16, metrics, lifecycle);
        lifecycle.setWriteChannel(channel);

        channel.markProducerDone(); // no frames ever offered — e.g. an empty upstream stream

        waitUntil(lifecycle::isTerminal);
        assertThat(lifecycle.outcome()).isEqualTo("completed");
        exec.shutdownNow();
    }

    /**
     * Lost-wakeup stress test (Unit 2 §13/§21 — "단순 1회가 아니라... 여러 번 반복해 stranded
     * frame이 없는지 검증"). Many independent iterations, each with many producer threads racing a
     * small drain pool, reconciling accepted-offer count against actually-written count every
     * time. A lost-wakeup bug would show up as writtenCount < acceptedCount (a frame stuck in the
     * buffer with draining permanently false and no task left to drain it).
     */
    @Test
    void noStrandedFramesAcrossManyRepeatedRaceIterations() throws Exception {
        int iterations = 30;
        int producers = 6;
        int framesPerProducer = 40;

        for (int iterIndex = 0; iterIndex < iterations; iterIndex++) {
            final int iter = iterIndex;
            RecordingWriter raw = new RecordingWriter();
            PrintWriter writer = new PrintWriter(raw);
            GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
            RequestLifecycle lifecycle = newLifecycle(metrics);
            ThreadPoolExecutor exec = newWriteExecutor(3, 1000);
            int capacity = producers * framesPerProducer + 10; // large enough to avoid overflow noise
            PerStreamWriteChannel channel = new PerStreamWriteChannel(writer, exec, capacity, metrics, lifecycle);

            AtomicInteger accepted = new AtomicInteger(0);
            ExecutorService producerPool = Executors.newFixedThreadPool(producers);
            CountDownLatch start = new CountDownLatch(1);
            CountDownLatch done = new CountDownLatch(producers);
            for (int p = 0; p < producers; p++) {
                producerPool.submit(() -> {
                    try {
                        start.await();
                        for (int i = 0; i < framesPerProducer; i++) {
                            if (channel.offer("x\n")) {
                                accepted.incrementAndGet();
                            }
                        }
                    } catch (InterruptedException ignored) {
                    } finally {
                        done.countDown();
                    }
                });
            }
            start.countDown();
            assertThat(done.await(10, TimeUnit.SECONDS)).isTrue();
            producerPool.shutdown();

            waitUntilOrFail(() -> raw.written.size() == accepted.get(), 3000,
                    () -> "iteration " + iter + ": accepted=" + accepted.get() + " written=" + raw.written.size()
                            + " (stranded frame — lost-wakeup bug)");

            exec.shutdownNow();
        }
    }

    private static void waitUntil(java.util.function.BooleanSupplier condition) throws InterruptedException {
        waitUntilOrFail(condition, 5000, () -> "condition not met within timeout");
    }

    private static void waitUntilOrFail(java.util.function.BooleanSupplier condition, long timeoutMs,
            java.util.function.Supplier<String> failureMessage) throws InterruptedException {
        long deadline = System.currentTimeMillis() + timeoutMs;
        while (System.currentTimeMillis() < deadline) {
            if (condition.getAsBoolean()) {
                return;
            }
            Thread.sleep(5);
        }
        assertThat(condition.getAsBoolean()).as(failureMessage.get()).isTrue();
    }
}
