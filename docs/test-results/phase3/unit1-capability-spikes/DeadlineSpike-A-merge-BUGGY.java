import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;
import reactor.core.publisher.Sinks;
import reactor.core.Disposable;

import java.time.Duration;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Scratch verification (Phase 3 Unit 1, not shipped code): does
 * Flux.merge(data, deadlineErrorMono) give a true ABSOLUTE deadline
 * (fixed from subscription time, independent of onNext activity), with
 * correct cancellation propagation and no timer leak, for the four
 * lifecycle cases required by the contract:
 *   1. normal completion before deadline -> timer cancelled, no error
 *   2. deadline fires before completion   -> downstream error, upstream data cancelled
 *   3. downstream/client cancels early    -> both upstream data AND timer cancelled
 *   4. upstream data errors first         -> timer cancelled, original error propagates
 */
public class DeadlineSpike {

    static Flux<Integer> withAbsoluteDeadline(Flux<Integer> data, Duration deadline) {
        Mono<Integer> deadlineSignal = Mono.<Integer>delay(deadline)
                .flatMap(tick -> Mono.error(new TimeoutException("absolute-deadline-exceeded")));
        return Flux.merge(data, deadlineSignal.flux());
    }

    public static void main(String[] args) throws Exception {
        case1_normalCompletionBeforeDeadline();
        case2_deadlineFiresBeforeCompletion();
        case3_downstreamCancelsEarly();
        case4_upstreamErrorsFirst();
        System.out.println("ALL CASES DONE");
    }

    // Case 1: data emits 3 items quickly and completes well before the 500ms deadline.
    static void case1_normalCompletionBeforeDeadline() throws Exception {
        AtomicBoolean dataCancelled = new AtomicBoolean(false);
        AtomicBoolean timerSubscribed = new AtomicBoolean(false);
        AtomicBoolean timerCancelledOrCompleted = new AtomicBoolean(false);

        Flux<Integer> data = Flux.just(1, 2, 3)
                .delayElements(Duration.ofMillis(20))
                .doOnCancel(() -> dataCancelled.set(true));

        Mono<Integer> deadlineSignal = Mono.<Integer>delay(Duration.ofMillis(500))
                .doOnSubscribe(s -> timerSubscribed.set(true))
                .doOnCancel(() -> timerCancelledOrCompleted.set(true))
                .flatMap(tick -> Mono.error(new TimeoutException("absolute-deadline-exceeded")));

        Flux<Integer> merged = Flux.merge(data, deadlineSignal.flux());

        CountDownLatch latch = new CountDownLatch(1);
        AtomicInteger count = new AtomicInteger(0);
        AtomicBoolean sawError = new AtomicBoolean(false);
        merged.subscribe(
                v -> count.incrementAndGet(),
                e -> { sawError.set(true); latch.countDown(); },
                latch::countDown
        );
        latch.await(2, TimeUnit.SECONDS);
        Thread.sleep(100); // let doOnCancel/dispose settle
        System.out.println("[case1 normal-completion] items=" + count.get()
                + " sawError=" + sawError.get()
                + " timerSubscribed=" + timerSubscribed.get()
                + " timerCancelled=" + timerCancelledOrCompleted.get()
                + " dataCancelled=" + dataCancelled.get());
    }

    // Case 2: data emits slowly (never completes within test window); 300ms deadline should fire first.
    static void case2_deadlineFiresBeforeCompletion() throws Exception {
        AtomicBoolean dataCancelled = new AtomicBoolean(false);

        Flux<Integer> data = Flux.interval(Duration.ofMillis(100))
                .map(Long::intValue)
                .doOnCancel(() -> dataCancelled.set(true));

        Flux<Integer> merged = withAbsoluteDeadline(data, Duration.ofMillis(300));

        CountDownLatch latch = new CountDownLatch(1);
        AtomicInteger count = new AtomicInteger(0);
        final Throwable[] err = new Throwable[1];
        long t0 = System.nanoTime();
        merged.subscribe(
                v -> count.incrementAndGet(),
                e -> { err[0] = e; latch.countDown(); },
                latch::countDown
        );
        latch.await(2, TimeUnit.SECONDS);
        long elapsedMs = (System.nanoTime() - t0) / 1_000_000;
        Thread.sleep(150); // allow cancellation to propagate to doOnCancel
        System.out.println("[case2 deadline-fires-first] items=" + count.get()
                + " error=" + (err[0] == null ? "none" : err[0].getClass().getSimpleName() + ":" + err[0].getMessage())
                + " elapsedMs=" + elapsedMs
                + " dataCancelledAfterDeadline=" + dataCancelled.get());
    }

    // Case 3: downstream subscriber cancels 150ms in, well before both the data's next
    // emission and the 5s deadline — both the data source and the deadline timer must
    // be cancelled (no leaked timer, no leaked upstream subscription).
    static void case3_downstreamCancelsEarly() throws Exception {
        AtomicBoolean dataCancelled = new AtomicBoolean(false);
        AtomicBoolean timerCancelled = new AtomicBoolean(false);

        Flux<Integer> data = Flux.interval(Duration.ofMillis(1000))
                .map(Long::intValue)
                .doOnCancel(() -> dataCancelled.set(true));

        Mono<Integer> deadlineSignal = Mono.<Integer>delay(Duration.ofSeconds(5))
                .doOnCancel(() -> timerCancelled.set(true))
                .flatMap(tick -> Mono.error(new TimeoutException("absolute-deadline-exceeded")));

        Flux<Integer> merged = Flux.merge(data, deadlineSignal.flux());

        Disposable subscription = merged.subscribe(v -> {}, e -> {}, () -> {});
        Thread.sleep(150);
        subscription.dispose();
        Thread.sleep(150); // allow cancellation to propagate
        System.out.println("[case3 downstream-cancel] dataCancelled=" + dataCancelled.get()
                + " timerCancelled=" + timerCancelled.get());
    }

    // Case 4: the data source itself errors (simulating upstream_error) before the deadline —
    // the timer must be cancelled (no leak) and the original error must propagate unchanged.
    static void case4_upstreamErrorsFirst() throws Exception {
        AtomicBoolean timerCancelled = new AtomicBoolean(false);

        Flux<Integer> data = Flux.<Integer>error(new IllegalStateException("simulated-upstream-error"))
                .delaySubscription(Duration.ofMillis(50));

        Mono<Integer> deadlineSignal = Mono.<Integer>delay(Duration.ofSeconds(5))
                .doOnCancel(() -> timerCancelled.set(true))
                .flatMap(tick -> Mono.error(new TimeoutException("absolute-deadline-exceeded")));

        Flux<Integer> merged = Flux.merge(data, deadlineSignal.flux());

        CountDownLatch latch = new CountDownLatch(1);
        final Throwable[] err = new Throwable[1];
        merged.subscribe(v -> {}, e -> { err[0] = e; latch.countDown(); }, latch::countDown);
        latch.await(2, TimeUnit.SECONDS);
        Thread.sleep(150);
        System.out.println("[case4 upstream-error-first] error=" + (err[0] == null ? "none" : err[0].getClass().getSimpleName() + ":" + err[0].getMessage())
                + " timerCancelled=" + timerCancelled.get());
    }
}
