package com.llmconcurrencylab.phase4.common;

import java.io.PrintWriter;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Request-local serialized write channel over a shared bounded write executor — the common M1/M2
 * Servlet write path (docs/test-plan/phase4-design.md section 0-2 / section 6). One instance per
 * in-flight request; the producer (relay thread, platform or virtual) calls {@link #offer} and
 * {@link #markProducerDone}, and a shared write-executor thread performs the actual
 * {@link PrintWriter} write — never the producer thread and never more than one write-executor
 * thread for this stream at a time (single-flight drain, ordering preserved).
 *
 * <p>Final-drain semantic (docs/decisions/phase3-timeout-cancellation.md section 0 addendum):
 * upstream EOF ({@link #markProducerDone}) only means "no more frames will be offered" — this
 * class itself decides when the request becomes {@code completed}, only once its buffer has
 * actually drained to empty. This is what prevents the final-frame-loss bug Phase 3 hit.
 */
public final class PerStreamWriteChannel {

    private static final Logger log = LoggerFactory.getLogger(PerStreamWriteChannel.class);
    // Env-overridable ONLY for F8 (write-overflow) functional testing; Formal harness must not
    // override — default frozen at 8 (docs/test-plan/phase4-design.md section 0-2).
    private static final int PER_STREAM_CAPACITY = EnvUtil.getInt("SERVLET_PER_STREAM_BUFFER_CAPACITY", 8);

    private final ArrayBlockingQueue<SseFrame> queue = new ArrayBlockingQueue<>(PER_STREAM_CAPACITY);
    private final AtomicBoolean producerDone = new AtomicBoolean(false);
    private final AtomicBoolean drainScheduled = new AtomicBoolean(false);

    private final RequestLifecycle lifecycle;
    private final PrintWriter writer;
    private final ThreadPoolExecutor writeExecutor;
    private final GatewayMetrics metrics;
    private final WriteChannelMetrics writeMetrics;
    private final Consumer<Outcome> onTerminal;

    public PerStreamWriteChannel(RequestLifecycle lifecycle, PrintWriter writer, ThreadPoolExecutor writeExecutor,
            GatewayMetrics metrics, WriteChannelMetrics writeMetrics, Consumer<Outcome> onTerminal) {
        this.lifecycle = lifecycle;
        this.writer = writer;
        this.writeExecutor = writeExecutor;
        this.metrics = metrics;
        this.writeMetrics = writeMetrics;
        this.onTerminal = onTerminal;
    }

    /** Producer-thread call. @return false if the frame was rejected (overflow — already terminal). */
    public boolean offer(SseFrame frame) {
        if (lifecycle.isTerminal()) {
            return false;
        }
        boolean accepted = queue.offer(frame);
        if (!accepted) {
            writeMetrics.overflowPerStream();
            if (lifecycle.tryTerminate(Outcome.WRITE_OVERFLOW)) {
                terminateNow(Outcome.WRITE_OVERFLOW);
            }
            return false;
        }
        writeMetrics.frameBuffered();
        scheduleDrainIfNeeded();
        return true;
    }

    /** Producer-thread call — upstream reached EOF normally. Does not itself decide the outcome. */
    public void markProducerDone() {
        producerDone.set(true);
        scheduleDrainIfNeeded();
    }

    /**
     * Used when a non-completion terminal outcome (error/disconnect/timeout/overflow) already won
     * the lifecycle CAS — discards any buffered frames (they will never reach the client) and runs
     * the terminal callback exactly once.
     */
    public void terminateNow(Outcome outcome) {
        while (queue.poll() != null) {
            writeMetrics.frameDrained();
        }
        onTerminal.accept(outcome);
    }

    private void scheduleDrainIfNeeded() {
        if (drainScheduled.compareAndSet(false, true)) {
            try {
                writeExecutor.execute(this::drainLoop);
            } catch (RejectedExecutionException e) {
                drainScheduled.set(false);
                writeMetrics.overflowExecutor();
                if (lifecycle.tryTerminate(Outcome.WRITE_OVERFLOW)) {
                    terminateNow(Outcome.WRITE_OVERFLOW);
                }
            }
        }
    }

    /** Runs on a shared write-executor thread. Single-flight: only one drainLoop per stream at a time. */
    private void drainLoop() {
        do {
            SseFrame frame;
            while ((frame = queue.poll()) != null) {
                writeMetrics.frameDrained();
                writer.write(frame.format());
                writer.flush();
                metrics.bytesRelayed(frame.format().length());
                if (writer.checkError()) {
                    drainScheduled.set(false);
                    if (lifecycle.tryTerminate(Outcome.CLIENT_DISCONNECT)) {
                        terminateNow(Outcome.CLIENT_DISCONNECT);
                    }
                    return;
                }
            }
            drainScheduled.set(false);
            // Race window: an offer()/markProducerDone() may have happened after our last poll()
            // returned null but before drainScheduled was cleared. Re-check and reclaim the
            // single-flight token ourselves rather than relying on the racing caller to notice.
        } while (!queue.isEmpty() && drainScheduled.compareAndSet(false, true));

        maybeFinalizeCompleted();
    }

    private void maybeFinalizeCompleted() {
        if (producerDone.get() && queue.isEmpty() && !drainScheduled.get()) {
            if (lifecycle.tryTerminate(Outcome.COMPLETED)) {
                onTerminal.accept(Outcome.COMPLETED);
            }
        }
    }
}
