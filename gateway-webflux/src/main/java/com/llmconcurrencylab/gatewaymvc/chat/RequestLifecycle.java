package com.llmconcurrencylab.gatewaymvc.chat;

import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

import com.llmconcurrencylab.gatewaymvc.admission.AdmissionGate;
import com.llmconcurrencylab.gatewaymvc.metrics.GatewayMetrics;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * P3-C's lifecycle object, deliberately NOT a mechanical port of P3-A/B's RequestLifecycle
 * (Unit 4 §9). P3-A/B needed to own several external resources (AsyncContext, PrintWriter/write
 * channel, HttpURLConnection or a Disposable, a deadline watchdog Future) specifically because
 * their response path is decoupled from the upstream subscription — a Servlet write executor
 * drains a buffer independently of when the upstream call finishes. P3-C has no such split: the
 * response body IS one continuous Reactor chain from admission through the final emitted SSE
 * event, so there is nothing left for this class to separately track or race-close against — the
 * chain's own {@code doOnComplete}/{@code doOnError}/{@code doOnCancel} operators (wired in
 * {@code ChatController}) are what call {@link #tryTerminate(String)}, and Reactor's own
 * subscription teardown is what cancels the upstream WebClient call. This class only owns what
 * Unit 4 §9 actually asks for: the terminal CAS, and exactly-once permit release/metrics.
 */
public final class RequestLifecycle {

    private static final Logger log = LoggerFactory.getLogger(RequestLifecycle.class);

    private final String requestId;
    private final AdmissionGate admissionGate;
    private final GatewayMetrics metrics;
    private final long startNanos;

    private final AtomicReference<String> outcome = new AtomicReference<>(null);
    private final AtomicBoolean firstChunkRecorded = new AtomicBoolean(false);
    private final AtomicBoolean permitReleased = new AtomicBoolean(false);
    private volatile boolean admitted = false;

    public RequestLifecycle(String requestId, AdmissionGate admissionGate, GatewayMetrics metrics, long startNanos) {
        this.requestId = requestId;
        this.admissionGate = admissionGate;
        this.metrics = metrics;
        this.startNanos = startNanos;
    }

    public String requestId() {
        return requestId;
    }

    public boolean isTerminal() {
        return outcome.get() != null;
    }

    public String outcome() {
        return outcome.get();
    }

    public void markAdmitted() {
        this.admitted = true;
    }

    public void recordFirstChunkIfNeeded() {
        if (firstChunkRecorded.compareAndSet(false, true)) {
            metrics.recordFirstChunkRelay(System.nanoTime() - startNanos);
        }
    }

    /**
     * Releases the admission permit exactly once (Unit 6 admission-semantics unification,
     * docs/decisions/phase3-admission-semantics-unification.md) — decoupled from the full terminal
     * transition below so it can be called explicitly at the actual upstream {@code onComplete}
     * boundary ({@code ChatController.attachLifecycle()}'s {@code doOnComplete}), mirroring P3-A/B's
     * "release at natural upstream EOF" call site rather than only ever running as a side effect of
     * {@link #tryTerminate(String)}. For every other terminal outcome (error/timeout/cancel),
     * tearing down the upstream subscription already happens at the moment {@code tryTerminate} runs
     * — calling this from there too is already upstream-lifetime-aligned. The CAS here just makes
     * calling it from both places safe and exactly-once regardless of which one wins the race.
     *
     * <p>Note (documented, not hidden): P3-C has no separate response-buffering layer the way
     * P3-A/B's Servlet write executor does (Unit 4 §9's whole point), so for the "completed" happy
     * path this call and {@link #tryTerminate(String)}'s {@code activeStreamsDecrement()} currently
     * fire from the same {@code doOnComplete} callback — i.e. admission release and the response
     * outcome CAS happen at the same observable instant here, unlike P3-A/B where a real drain gap
     * can exist. This is the correct, honest consequence of P3-C's architecture (Unit 4 §5 forbids
     * adding an artificial buffering layer just to manufacture a gap) — the two concepts are still
     * kept code-level independent so the invariant in docs/decisions/
     * phase3-admission-semantics-unification.md holds by construction, not by coincidence.
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

        if (!"completed".equals(outcomeValue)) {
            metrics.recordUpstreamCancel();
        }

        releaseAdmissionPermit();

        metrics.recordOutcome(outcomeValue);
        metrics.activeStreamsDecrement();
        metrics.recordStreamDuration(System.nanoTime() - startNanos);

        log.info("[{}] terminal: outcome={}", requestId, outcomeValue);
        return true;
    }
}
