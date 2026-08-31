package com.llmconcurrencylab.phase4.common;

import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

import org.springframework.stereotype.Component;

/**
 * Shared single-thread deadline scheduler for M1/M2 absolute-deadline enforcement — the blocking
 * (non-Reactor) counterpart of the Reactor "Candidate C" absolute-deadline operator validated in
 * docs/decisions/phase3-timeout-cancellation.md section 3-3, applied here via the
 * remaining-budget-clamp + watchdog pattern from that document's section 4. Authoritative from
 * admission until {@link RequestLifecycle#tryTerminate} actually runs for ANY outcome — only
 * tryTerminate()'s own CAS win cancels/skips the watchdog action (idempotent-terminal principle,
 * same ADR section 9).
 */
@Component
public class DeadlineWatchdog {

    private final ScheduledExecutorService scheduler = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "phase4-deadline-watchdog");
        t.setDaemon(true);
        return t;
    });

    /**
     * Arms the watchdog for a request. {@code onDeadline} runs (on the watchdog thread) only if
     * this request has not already reached a terminal outcome by the time the deadline fires — the
     * TIMEOUT CAS itself is attempted here so the watchdog is the sole owner of that transition.
     */
    public void arm(RequestLifecycle lifecycle, Consumer<RequestLifecycle> onDeadline) {
        long delayMs = lifecycle.remainingMs();
        ScheduledFuture<?> future = scheduler.schedule(() -> {
            if (lifecycle.tryTerminate(Outcome.TIMEOUT)) {
                onDeadline.accept(lifecycle);
            }
        }, delayMs, TimeUnit.MILLISECONDS);
        lifecycle.setDeadlineFuture(future);
    }
}
