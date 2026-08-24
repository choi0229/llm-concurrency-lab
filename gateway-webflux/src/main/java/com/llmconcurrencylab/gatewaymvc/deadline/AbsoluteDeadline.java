package com.llmconcurrencylab.gatewaymvc.deadline;

import java.time.Duration;
import java.util.concurrent.atomic.AtomicBoolean;

import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

/**
 * The absolute-deadline operator verified in Unit 1
 * (docs/decisions/phase3-timeout-cancellation.md §3, candidate C — the only one of three tested
 * candidates that passed all four required lifecycle cases with no leak; see
 * docs/test-results/phase3/unit1-capability-spikes/DeadlineSpike-C-final-verified.java for the
 * original spike this is generalized from).
 *
 * <p><b>Not authoritative on its own in P3-B</b> (Unit 3 §8/§9/§10): applying this directly to the
 * WebClient upstream Flux only bounds the *upstream subscription* — it says nothing about whether
 * the Servlet write channel has actually finished draining the last frame yet. The lifecycle-owned
 * shared {@code ScheduledExecutorService} watchdog (the same mechanism P3-A uses,
 * {@code ChatController}) remains the single authoritative deadline for the whole request; this
 * operator is a supplementary mechanism that lets the Reactor subscription itself self-cancel at
 * the same instant, instead of relying solely on an external {@code Disposable.dispose()} call —
 * which is exactly the capability P3-A's blocking read lacks (Unit 2 Smoke B finding: {@code
 * HttpURLConnection.disconnect()} does not promptly unblock an in-flight blocked read). Both
 * mechanisms MUST be computed from the same {@code deadlineNanos} (never two independently-derived
 * durations) — see the caller in {@code ChatController}.
 *
 * <p>Why not just {@code Flux.merge(data, deadlineErrorMono)} or {@code
 * data.takeUntilOther(deadlineErrorMono)} alone: both were tried in Unit 1 and found to leak or
 * misreport normal completion as a timeout — see the ADR §3-1/§3-2 for the measured failures. This
 * class is the fix: the deadline signal always completes (never errors) so {@code takeUntilOther}
 * takes the branch that actually cancels the main sequence, and the real {@code TimeoutException}
 * is appended afterward, conditional on a flag, via {@code concatWith}.
 */
public final class AbsoluteDeadline {

    private AbsoluteDeadline() {
    }

    public static <T> Flux<T> apply(Flux<T> data, Duration remaining) {
        AtomicBoolean deadlineFired = new AtomicBoolean(false);
        Mono<Void> deadlineCompletionSignal = Mono.delay(remaining)
                .doOnNext(tick -> deadlineFired.set(true))
                .then();
        return data.takeUntilOther(deadlineCompletionSignal)
                .concatWith(Flux.defer(() -> deadlineFired.get()
                        ? Flux.error(new AbsoluteDeadlineExceededException())
                        : Flux.empty()));
    }
}
