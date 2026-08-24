package com.llmconcurrencylab.gatewaymvc.write;

import java.io.PrintWriter;
import java.util.ArrayDeque;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.atomic.AtomicBoolean;

import com.llmconcurrencylab.gatewaymvc.chat.RequestLifecycle;
import com.llmconcurrencylab.gatewaymvc.metrics.GatewayMetrics;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Request-local serialized write channel (docs/decisions/phase3-mvc-webclient-write-path.md §4,
 * Unit 2 §12/§13). One instance per stream. offer() is always non-blocking, safe to call from any
 * producer thread (P3-A's blocking outbound worker; later P3-B's Reactor event-loop must also stay
 * non-blocking here per the ADR §3 rule).
 *
 * <p>Design: bounded ArrayDeque buffer, guarded by a plain intrinsic lock (the buffer itself is
 * not thread-safe) + a single {@code draining} AtomicBoolean gate so that at most one drain task
 * is ever active for this stream at a time — this is what gives ordering and "no concurrent
 * PrintWriter.write()" for free, without a dedicated per-stream thread.
 *
 * <p><b>Lost-wakeup avoidance (Unit 2 §13):</b> a naive "if queue empty: draining=false; return"
 * drain loop has a race — a producer can enqueue a frame and see {@code draining==true} (so it
 * doesn't resubmit) in the narrow window between the consumer's own empty-check and its
 * {@code draining.set(false)}, stranding that frame with no task left to drain it. This
 * implementation closes that window by re-checking the buffer *after* clearing the flag and, if
 * it's non-empty, re-winning the CAS itself before giving up (see {@link #drainLoop(long)}).
 * {@code docs/test-results/phase3/unit2-p3a-functional/} and the unit tests in
 * {@code PerStreamWriteChannelTest} exercise this race directly (stress test, not a single-shot
 * check — Unit 2 §21).
 */
public class PerStreamWriteChannel {

    private static final Logger log = LoggerFactory.getLogger(PerStreamWriteChannel.class);

    private final Object lock = new Object();
    private final ArrayDeque<String> buffer = new ArrayDeque<>();
    private final int capacity;
    private final AtomicBoolean draining = new AtomicBoolean(false);
    private volatile boolean terminal = false;
    private volatile boolean producerDone = false;

    private final PrintWriter writer;
    private final ExecutorService writeExecutor;
    private final GatewayMetrics metrics;
    private final RequestLifecycle lifecycle;

    public PerStreamWriteChannel(PrintWriter writer, ExecutorService writeExecutor, int capacity,
            GatewayMetrics metrics, RequestLifecycle lifecycle) {
        this.writer = writer;
        this.writeExecutor = writeExecutor;
        this.capacity = capacity;
        this.metrics = metrics;
        this.lifecycle = lifecycle;
    }

    /**
     * @return true if the frame was accepted (enqueued, or already being drained); false if
     *     dropped — either because the channel is already terminal (completion/timeout/disconnect
     *     already happened — enqueue after that point is refused, Unit 2 §12) or because this call
     *     just caused an overflow (in which case the channel has just transitioned to terminal and
     *     the caller does not need to do anything further — the overflow path already triggered
     *     {@link RequestLifecycle#tryTerminate}).
     */
    public boolean offer(String frame) {
        boolean shouldSubmit = false;
        boolean overflowed = false;
        synchronized (lock) {
            if (terminal || producerDone) {
                return false;
            }
            if (buffer.size() >= capacity) {
                terminal = true;
                // Unit 5.5 D2 finding: a real slow-client run caught this branch clearing the
                // buffer WITHOUT decrementing streamBufferedFrames for the discarded entries —
                // unlike markTerminal()'s equivalent path — leaving the gauge permanently
                // overcounted after every overflow (observed stuck at capacity, then
                // 2x capacity after a second overflowing stream, never returning to 0). Postflight
                // invariant (docs/decisions/phase3-metrics-contract.md §6) requires
                // servlet_write_stream_buffered_frames == 0 once no stream is active.
                int discarded = buffer.size();
                buffer.clear();
                for (int i = 0; i < discarded; i++) {
                    metrics.streamBufferedFramesDecrement();
                }
                overflowed = true;
            } else {
                buffer.addLast(frame);
                metrics.streamBufferedFramesIncrement();
                if (draining.compareAndSet(false, true)) {
                    shouldSubmit = true;
                }
            }
        }
        if (overflowed) {
            metrics.writeOverflow.increment();
            log.warn("[{}] write buffer overflow (capacity={}), terminating stream", lifecycle.requestId(), capacity);
            lifecycle.tryTerminate("write_overflow");
            return false;
        }
        if (shouldSubmit) {
            return submitDrain();
        }
        return true;
    }

    /**
     * Signals natural upstream EOF (Mock LLM closed the stream cleanly — the "completed" outcome).
     * Unlike {@link #markTerminal()}, this does NOT discard whatever is still buffered — the whole
     * point is that the last frame(s) offered right before EOF (typically the {@code final} SSE
     * event) must still be written before the request is allowed to become terminal. Once the
     * buffer actually drains empty with this flag set, the drain loop itself calls
     * {@code lifecycle.tryTerminate("completed")} — never the caller of this method directly, so
     * "completed" can never fire before every already-accepted frame has actually been written.
     *
     * <p>P3-B-specific note (Unit 3 §6): this method is the "lifecycle signal" a Reactor Netty
     * event-loop thread is allowed to send directly (the ADR's callback rule permits that). But if
     * the buffer already happens to be empty at the moment this is called, the naive
     * implementation would run {@code lifecycle.tryTerminate("completed")} — which calls
     * {@code AsyncContext.complete()}, a Servlet-container operation — inline on THAT calling
     * thread. A Smoke A run caught this by logging real thread names: the terminal log line showed
     * up on a {@code reactor-http-nio-*} thread, not a {@code servlet-write-*} one (see
     * docs/test-results/phase3/unit3-p3b-functional/SUMMARY.md). No {@code PrintWriter} call is
     * involved in that path (markTerminal() only clears an already-empty buffer), so it isn't a
     * correctness bug, but it does cross the "Reactor callback never does Servlet-side work"
     * boundary the ADR asks for — so here (unlike P3-A's byte-identical copy of this class, which
     * never receives calls from a Reactor thread and therefore doesn't need this) the empty-buffer
     * fast path is dispatched through the shared write executor instead of run inline.
     */
    public void markProducerDone() {
        boolean shouldSubmit = false;
        boolean finalizeNow = false;
        synchronized (lock) {
            if (terminal || producerDone) {
                return;
            }
            producerDone = true;
            if (buffer.isEmpty()) {
                if (draining.compareAndSet(false, true)) {
                    finalizeNow = true;
                }
                // else: a drain task is already running and will observe producerDone==true on its
                // own next empty-check (drainLoop()'s lost-wakeup-safe recheck below).
            } else if (draining.compareAndSet(false, true)) {
                shouldSubmit = true;
            }
        }
        if (finalizeNow) {
            draining.set(false);
            try {
                writeExecutor.execute(() -> lifecycle.tryTerminate("completed"));
            } catch (RejectedExecutionException e) {
                // Degraded but still correct: better to finalize inline than to leave the request
                // hanging because the write executor happened to be saturated at this exact moment.
                log.warn("[{}] write executor rejected the completion-finalize task, finalizing inline",
                        lifecycle.requestId());
                lifecycle.tryTerminate("completed");
            }
        } else if (shouldSubmit) {
            submitDrain();
        }
    }

    /** Called by RequestLifecycle.tryTerminate() (exactly once) — discards any pending frames. */
    public void markTerminal() {
        synchronized (lock) {
            if (!terminal) {
                terminal = true;
                int discarded = buffer.size();
                buffer.clear();
                for (int i = 0; i < discarded; i++) {
                    metrics.streamBufferedFramesDecrement();
                }
            }
        }
    }

    private boolean submitDrain() {
        long submittedAtNanos = System.nanoTime();
        try {
            writeExecutor.execute(() -> drainLoop(submittedAtNanos));
            return true;
        } catch (RejectedExecutionException e) {
            // The drain task never started — draining must not stay stuck true, and the frames
            // already enqueued must not linger forever with nobody left to write them (Unit 2 §14).
            synchronized (lock) {
                int discarded = buffer.size();
                buffer.clear();
                for (int i = 0; i < discarded; i++) {
                    metrics.streamBufferedFramesDecrement();
                }
                terminal = true;
            }
            draining.set(false);
            metrics.servletWriteExecutorRejected.increment();
            log.warn("[{}] servlet write executor rejected drain task submission", lifecycle.requestId());
            // Not one of the 7 frozen outcomes on its own (Unit 2 §14 — "새 outcome을 임의 추가하지
            // 않는다") — an executor that is supposed to have capacity for every admitted request
            // rejecting a submission is an internal resource/config invariant violation, not a
            // normal overload signal (the same reasoning as the P3-A blocking executor in
            // docs/decisions/phase3-admission-connection-pool.md §2-3).
            lifecycle.tryTerminate("internal_error");
            return false;
        }
    }

    private void drainLoop(long firstSubmittedAtNanos) {
        boolean first = true;
        while (true) {
            String frame;
            synchronized (lock) {
                frame = buffer.pollFirst();
                if (frame != null) {
                    metrics.streamBufferedFramesDecrement();
                } else {
                    draining.set(false);
                }
            }
            if (frame == null) {
                // Lost-wakeup recheck (Unit 2 §13): a producer may have enqueued between our
                // pollFirst()==null and draining.set(false) above without seeing draining==false
                // in time to resubmit. Re-observe the buffer now that draining is false; if it's
                // non-empty and nobody else has already re-armed draining, we do it ourselves and
                // keep going instead of returning and stranding the frame. The same recheck also
                // catches markProducerDone() having been called in that same narrow window.
                boolean resume;
                boolean finalizeAsCompleted;
                synchronized (lock) {
                    resume = !terminal && !buffer.isEmpty() && draining.compareAndSet(false, true);
                    finalizeAsCompleted = !resume && !terminal && producerDone && draining.compareAndSet(false, true);
                }
                if (resume) {
                    continue;
                }
                if (finalizeAsCompleted) {
                    draining.set(false);
                    lifecycle.tryTerminate("completed");
                }
                return;
            }
            long queueWaitNanos = first ? System.nanoTime() - firstSubmittedAtNanos : 0;
            first = false;
            doWrite(frame, queueWaitNanos);
            if (terminal) {
                synchronized (lock) {
                    int discarded = buffer.size();
                    buffer.clear();
                    for (int i = 0; i < discarded; i++) {
                        metrics.streamBufferedFramesDecrement();
                    }
                }
                return;
            }
        }
    }

    private void doWrite(String frame, long queueWaitNanos) {
        long t0 = System.nanoTime();
        writer.write(frame);
        writer.flush();
        long durationNanos = System.nanoTime() - t0;
        metrics.recordWriteDuration(durationNanos);
        if (queueWaitNanos > 0) {
            metrics.recordWriteQueueWait(queueWaitNanos);
        }
        metrics.bytesRelayed.increment(frame.length());
        lifecycle.recordFirstChunkIfNeeded();
        // PrintWriter swallows IOExceptions; checkError() is the only signal (Unit 2 §15, ported
        // from Phase 1/2 ChatController.relay()). PrintWriter does not guarantee the bytes actually
        // reached the network — only that no write error has been observed yet.
        if (writer.checkError()) {
            terminal = true;
            log.info("[{}] client disconnected mid-stream (checkError)", lifecycle.requestId());
            lifecycle.tryTerminate("client_disconnect");
        }
    }
}
