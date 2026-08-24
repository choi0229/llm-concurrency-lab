import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;
import reactor.core.Disposable;

import java.time.Duration;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Candidate C (final): data.takeUntilOther(deadline-as-COMPLETION-signal),
 * with the deadline recorded via a side flag and re-injected as a real
 * TimeoutException via concatWith(Flux.defer(...)). Fixes the leak found in
 * DeadlineSpike2/3 (takeUntilOther does NOT cancel the main source when
 * "other" signals via onError - only when "other" signals onNext/onComplete).
 * By making "other" always complete (never error) and moving the actual
 * TimeoutException to a deferred tail appended after takeUntilOther's own
 * termination, we get: main cancelled on deadline (because "other"
 * completes, the path that DOES cancel main), AND a real error surfaced
 * downstream (via the deferred tail), AND no change to the already-correct
 * normal-completion / downstream-cancel / upstream-error-first paths.
 */
public class DeadlineSpike4 {

    static Flux<Integer> withAbsoluteDeadline(Flux<Integer> data, Duration deadline) {
        AtomicBoolean deadlineFired = new AtomicBoolean(false);
        Mono<Void> deadlineCompletionSignal = Mono.delay(deadline)
                .doOnNext(t -> deadlineFired.set(true))
                .then();
        return data.takeUntilOther(deadlineCompletionSignal)
                .concatWith(Flux.defer(() -> deadlineFired.get()
                        ? Flux.error(new TimeoutException("absolute-deadline-exceeded"))
                        : Flux.empty()));
    }

    public static void main(String[] args) throws Exception {
        case1_normalCompletionBeforeDeadline();
        case2_deadlineFiresBeforeCompletion_noLeak();
        case3_downstreamCancelsEarly();
        case4_upstreamErrorsFirst();
        System.out.println("ALL CASES DONE");
    }

    static void case1_normalCompletionBeforeDeadline() throws Exception {
        Flux<Integer> data = Flux.just(1, 2, 3).delayElements(Duration.ofMillis(20));
        Flux<Integer> merged = withAbsoluteDeadline(data, Duration.ofMillis(500));

        CountDownLatch latch = new CountDownLatch(1);
        AtomicInteger count = new AtomicInteger(0);
        AtomicBoolean sawError = new AtomicBoolean(false);
        long t0 = System.nanoTime();
        merged.subscribe(v -> count.incrementAndGet(), e -> { sawError.set(true); latch.countDown(); }, latch::countDown);
        latch.await(2, TimeUnit.SECONDS);
        long elapsedMs = (System.nanoTime() - t0) / 1_000_000;
        System.out.println("[case1 normal-completion] items=" + count.get()
                + " sawError=" + sawError.get() + " elapsedMs=" + elapsedMs
                + " (expect: items=3 sawError=false elapsedMs<<500, proving the timer branch does not block early completion)");
    }

    static void case2_deadlineFiresBeforeCompletion_noLeak() throws Exception {
        AtomicInteger rawTicks = new AtomicInteger(0);
        Flux<Integer> data = Flux.interval(Duration.ofMillis(100))
                .doOnNext(t -> rawTicks.incrementAndGet())
                .map(Long::intValue);
        Flux<Integer> merged = withAbsoluteDeadline(data, Duration.ofMillis(300));

        CountDownLatch latch = new CountDownLatch(1);
        AtomicInteger count = new AtomicInteger(0);
        final Throwable[] err = new Throwable[1];
        long t0 = System.nanoTime();
        merged.subscribe(v -> count.incrementAndGet(), e -> { err[0] = e; latch.countDown(); }, latch::countDown);
        latch.await(2, TimeUnit.SECONDS);
        long elapsedMs = (System.nanoTime() - t0) / 1_000_000;
        int ticksAtTermination = rawTicks.get();
        Thread.sleep(2000);
        int ticksAfter2s = rawTicks.get();
        System.out.println("[case2 deadline-fires-first] items=" + count.get()
                + " error=" + (err[0] == null ? "none" : err[0].getClass().getSimpleName() + ":" + err[0].getMessage())
                + " elapsedMs=" + elapsedMs
                + " rawTicksAtTermination=" + ticksAtTermination
                + " rawTicksAfter+2000ms=" + ticksAfter2s
                + " LEAK=" + (ticksAfter2s > ticksAtTermination));
    }

    static void case3_downstreamCancelsEarly() throws Exception {
        AtomicBoolean dataCancelled = new AtomicBoolean(false);
        AtomicInteger rawTicks = new AtomicInteger(0);
        Flux<Integer> data = Flux.interval(Duration.ofMillis(1000)).map(Long::intValue)
                .doOnCancel(() -> dataCancelled.set(true))
                .doOnNext(t -> rawTicks.incrementAndGet());
        Flux<Integer> merged = withAbsoluteDeadline(data, Duration.ofSeconds(5));

        Disposable subscription = merged.subscribe(v -> {}, e -> {}, () -> {});
        Thread.sleep(150);
        subscription.dispose();
        Thread.sleep(150);
        System.out.println("[case3 downstream-cancel] dataCancelled=" + dataCancelled.get());
    }

    static void case4_upstreamErrorsFirst() throws Exception {
        Flux<Integer> data = Flux.<Integer>error(new IllegalStateException("simulated-upstream-error"))
                .delaySubscription(Duration.ofMillis(50));
        Flux<Integer> merged = withAbsoluteDeadline(data, Duration.ofSeconds(5));

        CountDownLatch latch = new CountDownLatch(1);
        final Throwable[] err = new Throwable[1];
        merged.subscribe(v -> {}, e -> { err[0] = e; latch.countDown(); }, latch::countDown);
        latch.await(2, TimeUnit.SECONDS);
        System.out.println("[case4 upstream-error-first] error=" + (err[0] == null ? "none" : err[0].getClass().getSimpleName() + ":" + err[0].getMessage()));
    }
}
