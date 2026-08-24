package com.llmconcurrencylab.gatewaymvc.chat;

import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

import com.llmconcurrencylab.gatewaymvc.admission.AdmissionGate;
import com.llmconcurrencylab.gatewaymvc.metrics.GatewayMetrics;
import com.llmconcurrencylab.gatewaymvc.write.PerStreamWriteChannel;
import javax.servlet.AsyncContext;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import reactor.core.Disposable;

/**
 * Ported from gateway-mvc-blocking-spring5 (P3-A)'s RequestLifecycle — Unit 3 §7's one prescribed
 * structural change: P3-A's {@code HttpURLConnection connection} + {@code Future<?> outboundFuture}
 * fields (there is no separate "outbound task" here — the WebClient subscription itself IS both
 * the connection handle and the cancellable unit of work) are replaced by a single
 * {@code Disposable upstreamSubscription} field. Everything else — the terminal CAS, exactly-once
 * cleanup order, the race-closing setters, first-chunk recording, phase tracking — is unchanged
 * (docs/decisions/phase3-timeout-cancellation.md §9, Unit 2 §6). See the module README's
 * P3-A/P3-B structural comparison table for the full diff.
 */
public final class RequestLifecycle {

    public enum Phase { STARTED, ADMITTED, UPSTREAM_ACTIVE, STREAMING, TERMINAL }

    private static final Logger log = LoggerFactory.getLogger(RequestLifecycle.class);

    private final String requestId;
    private final AsyncContext asyncContext;
    private final AdmissionGate admissionGate;
    private final GatewayMetrics metrics;
    private final long startNanos;

    private final AtomicReference<String> outcome = new AtomicReference<>(null);
    private final AtomicBoolean firstChunkRecorded = new AtomicBoolean(false);
    private final AtomicBoolean permitReleased = new AtomicBoolean(false);
    private volatile Phase phase = Phase.STARTED;
    private volatile boolean admitted = false;

    private volatile Disposable upstreamSubscription;
    private volatile ScheduledFuture<?> deadlineTask;
    private volatile PerStreamWriteChannel writeChannel;

    public RequestLifecycle(String requestId, AsyncContext asyncContext, AdmissionGate admissionGate,
            GatewayMetrics metrics, long startNanos) {
        this.requestId = requestId;
        this.asyncContext = asyncContext;
        this.admissionGate = admissionGate;
        this.metrics = metrics;
        this.startNanos = startNanos;
    }

    public String requestId() {
        return requestId;
    }

    public long startNanos() {
        return startNanos;
    }

    public boolean isTerminal() {
        return outcome.get() != null;
    }

    public String outcome() {
        return outcome.get();
    }

    public Phase phase() {
        return phase;
    }

    public void markAdmitted() {
        this.admitted = true;
        this.phase = Phase.ADMITTED;
    }

    public void markUpstreamActive() {
        if (!isTerminal()) {
            this.phase = Phase.UPSTREAM_ACTIVE;
        }
    }

    public void markStreaming() {
        if (!isTerminal()) {
            this.phase = Phase.STREAMING;
        }
    }

    /** If already terminal, disposes immediately instead of leaving the subscription running. */
    public void setUpstreamSubscription(Disposable subscription) {
        this.upstreamSubscription = subscription;
        if (isTerminal()) {
            safeDispose(subscription);
        }
    }

    /** If already terminal, cancels immediately — avoids scheduling a timeout that can never matter. */
    public void setDeadlineTask(ScheduledFuture<?> task) {
        this.deadlineTask = task;
        if (isTerminal()) {
            task.cancel(false);
        }
    }

    /** If already terminal, marks the channel terminal immediately so it never accepts a frame. */
    public void setWriteChannel(PerStreamWriteChannel channel) {
        this.writeChannel = channel;
        if (isTerminal()) {
            channel.markTerminal();
        }
    }

    public void recordFirstChunkIfNeeded() {
        if (firstChunkRecorded.compareAndSet(false, true)) {
            metrics.recordFirstChunkRelay(System.nanoTime() - startNanos);
        }
    }

    /**
     * Releases the admission permit exactly once (Unit 6 admission-semantics unification,
     * docs/decisions/phase3-admission-semantics-unification.md) — decoupled from the full terminal
     * transition below so the "completed" happy path can release upstream capacity at natural
     * upstream EOF (called from {@code ChatController.onUpstreamComplete()}) instead of waiting for
     * the Servlet write channel to finish draining to the client. Every other terminal outcome
     * already tears down the upstream subscription at the moment {@link #tryTerminate(String)} runs,
     * so calling this from there too is already upstream-lifetime-aligned — this method's CAS just
     * makes calling it from both places safe and exactly-once regardless of which one wins the race.
     */
    public void releaseAdmissionPermit() {
        if (admitted && permitReleased.compareAndSet(false, true)) {
            admissionGate.release();
            metrics.admissionActiveDecrement();
        }
    }

    /**
     * @return true if this call performed the terminal transition (and therefore ran cleanup);
     *     false if some other caller already won.
     */
    public boolean tryTerminate(String outcomeValue) {
        if (!outcome.compareAndSet(null, outcomeValue)) {
            return false;
        }
        phase = Phase.TERMINAL;

        ScheduledFuture<?> dt = deadlineTask;
        if (dt != null) {
            dt.cancel(false);
        }

        PerStreamWriteChannel wc = writeChannel;
        if (wc != null) {
            wc.markTerminal();
        }

        Disposable sub = upstreamSubscription;
        if (sub != null) {
            safeDispose(sub);
        }

        // Unit 5.5 §0 metric-bookkeeping correction: gateway.upstream.cancel means "Gateway asked
        // an ALREADY-STARTED upstream call/connection/subscription to cancel" — "rejected" never
        // reaches openStream() at all (admission fails before any upstream interaction is
        // attempted), so it must not count here. P3-C's equivalent RequestLifecycle never even
        // gets constructed for the rejected path, so it never needed this exclusion; P3-A/B's
        // shared control flow does construct one, so the exclusion is added here explicitly. No
        // other outcome is excluded — this is the one gap Unit 5's F3 cross-validation found.
        if (!"completed".equals(outcomeValue) && !"rejected".equals(outcomeValue)) {
            metrics.recordUpstreamCancel();
        }

        releaseAdmissionPermit();

        metrics.recordOutcome(outcomeValue);
        metrics.activeStreamsDecrement();
        metrics.recordStreamDuration(System.nanoTime() - startNanos);

        try {
            asyncContext.complete();
        } catch (IllegalStateException e) {
            // Already completed via another path (e.g. container's own AsyncListener.onComplete
            // firing after we already completed it ourselves) — harmless, matches Phase 1/2's
            // AsyncRequestState.releaseOnce() javadoc on the same race.
        }

        log.info("[{}] terminal: outcome={}", requestId, outcomeValue);
        return true;
    }

    private static void safeDispose(Disposable disposable) {
        try {
            if (!disposable.isDisposed()) {
                disposable.dispose();
            }
        } catch (RuntimeException e) {
            log.debug("dispose() threw while cleaning up (ignored): {}", e.toString());
        }
    }
}
