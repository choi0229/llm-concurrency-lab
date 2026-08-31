package com.llmconcurrencylab.phase4.common;

import java.net.HttpURLConnection;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Request-scoped lifecycle state, identical for M1 and M2 (docs/test-plan/phase4-design.md
 * section 9 "Request Population" and section 11 "Cancellation Contract"). Owns the single
 * terminal-outcome CAS gate — exactly one caller among {rejection, deadline watchdog, disconnect
 * detector, upstream relay, write channel} ever wins tryTerminate() for a given request
 * (docs/decisions/phase3-timeout-cancellation.md section 9 idempotent-terminal pattern, carried
 * into Phase 4 as the same design idiom, reimplemented fresh for this module).
 */
public final class RequestLifecycle {

    private final String requestId;
    private final long startNanos;
    private final long deadlineNanos;
    private final AtomicReference<Outcome> terminalOutcome = new AtomicReference<>();
    private volatile HttpURLConnection activeConnection;
    private volatile ScheduledFuture<?> deadlineFuture;
    private volatile Runnable cancelHook;

    public RequestLifecycle(String requestId, long totalTimeoutMs) {
        this.requestId = requestId;
        this.startNanos = System.nanoTime();
        this.deadlineNanos = startNanos + totalTimeoutMs * 1_000_000L;
    }

    public String requestId() {
        return requestId;
    }

    public long startNanos() {
        return startNanos;
    }

    /** Remaining budget in ms, clamped to >=0, for outbound connect/read timeout clamping. */
    public long remainingMs() {
        long remainingNanos = deadlineNanos - System.nanoTime();
        return Math.max(0L, remainingNanos / 1_000_000L);
    }

    public boolean isPastDeadline() {
        return System.nanoTime() >= deadlineNanos;
    }

    public boolean isTerminal() {
        return terminalOutcome.get() != null;
    }

    public Outcome terminalOutcomeOrNull() {
        return terminalOutcome.get();
    }

    /**
     * @return true iff this call won the terminal CAS (first and only winner for this request).
     * Runs the cancel hook (queue removal, etc.) exactly once, synchronously, before returning —
     * every winning path gets the hook for free rather than each caller remembering to invoke it.
     */
    public boolean tryTerminate(Outcome outcome) {
        boolean won = terminalOutcome.compareAndSet(null, outcome);
        if (won) {
            Runnable hook = this.cancelHook;
            if (hook != null) {
                hook.run();
            }
        }
        return won;
    }

    public void setActiveConnection(HttpURLConnection connection) {
        this.activeConnection = connection;
    }

    /** Force-unblocks a mid-relay blocking read (deadline watchdog / disconnect path). */
    public void disconnectActiveConnectionIfAny() {
        HttpURLConnection c = this.activeConnection;
        if (c != null) {
            c.disconnect();
        }
    }

    public void setDeadlineFuture(ScheduledFuture<?> future) {
        this.deadlineFuture = future;
    }

    public void cancelDeadlineFuture() {
        ScheduledFuture<?> f = this.deadlineFuture;
        if (f != null) {
            f.cancel(false);
        }
    }

    /** M1: removes the (possibly still-queued) task from the executor if terminal wins early. */
    public void setCancelHook(Runnable hook) {
        this.cancelHook = hook;
    }
}
