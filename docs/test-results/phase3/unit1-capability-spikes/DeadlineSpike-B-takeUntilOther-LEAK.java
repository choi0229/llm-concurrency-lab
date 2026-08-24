import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

import java.time.Duration;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import reactor.core.Disposable;

/**
 * Candidate B (corrected): data.takeUntilOther(deadlineErrorMono) instead of
 * Flux.merge(data, deadlineErrorMono). DeadlineSpike.java (candidate A,
 * Flux.merge) demonstrated a real bug: Flux.merge only completes once ALL
 * merged sources complete, so a never-otherwise-completing deadline Mono
 * blocks the merged Flux from completing on data's normal EOF until the
 * deadline Mono itself also terminates (by erroring) - i.e. every normal
 * completion incorrectly turns into a timeout error once the deadline fires
 * afterwards. This candidate tests whether takeUntilOther avoids that.
 */
public class DeadlineSpike2 {

    static Flux<Integer> withAbsoluteDeadline(Flux<Integer> data, Duration deadline) {
        Mono<Integer> deadlineSignal = Mono.<Integer>delay(deadline)
                .flatMap(tick -> Mono.error(new TimeoutException("absolute-deadline-exceeded")));
        return data.takeUntilOther(deadlineSignal);
    }

    public static void main(String[] args) throws Exception {
        case1_normalCompletionBeforeDeadline();
        case2_deadlineFiresBeforeCompletion();
        case3_downstreamCancelsEarly();
        case4_upstreamErrorsFirst();
        System.out.println("ALL CASES DONE");
    }

    static void case1_normalCompletionBeforeDeadline() throws Exception {
        AtomicBoolean timerCancelled = new AtomicBoolean(false);
        Flux<Integer> data = Flux.just(1, 2, 3).delayElements(Duration.ofMillis(20));
        Mono<Integer> deadlineSignal = Mono.<Integer>delay(Duration.ofMillis(500))
                .doOnCancel(() -> timerCancelled.set(true))
                .flatMap(tick -> Mono.error(new TimeoutException("absolute-deadline-exceeded")));
        Flux<Integer> merged = data.takeUntilOther(deadlineSignal);

        CountDownLatch latch = new CountDownLatch(1);
        AtomicInteger count = new AtomicInteger(0);
        AtomicBoolean sawError = new AtomicBoolean(false);
        long t0 = System.nanoTime();
        merged.subscribe(v -> count.incrementAndGet(), e -> { sawError.set(true); latch.countDown(); }, latch::countDown);
        latch.await(2, TimeUnit.SECONDS);
        long elapsedMs = (System.nanoTime() - t0) / 1_000_000;
        Thread.sleep(100);
        System.out.println("[case1 normal-completion] items=" + count.get()
                + " sawError=" + sawError.get() + " elapsedMs=" + elapsedMs
                + " timerCancelledPromptly=" + timerCancelled.get());
    }

    static void case2_deadlineFiresBeforeCompletion() throws Exception {
        AtomicBoolean dataCancelled = new AtomicBoolean(false);
        Flux<Integer> data = Flux.interval(Duration.ofMillis(100)).map(Long::intValue)
                .doOnCancel(() -> dataCancelled.set(true));
        Flux<Integer> merged = withAbsoluteDeadline(data, Duration.ofMillis(300));

        CountDownLatch latch = new CountDownLatch(1);
        AtomicInteger count = new AtomicInteger(0);
        final Throwable[] err = new Throwable[1];
        long t0 = System.nanoTime();
        merged.subscribe(v -> count.incrementAndGet(), e -> { err[0] = e; latch.countDown(); }, latch::countDown);
        latch.await(2, TimeUnit.SECONDS);
        long elapsedMs = (System.nanoTime() - t0) / 1_000_000;
        Thread.sleep(150);
        System.out.println("[case2 deadline-fires-first] items=" + count.get()
                + " error=" + (err[0] == null ? "none" : err[0].getClass().getSimpleName() + ":" + err[0].getMessage())
                + " elapsedMs=" + elapsedMs + " dataCancelledAt+150ms=" + dataCancelled.get());
        Thread.sleep(1000);
        System.out.println("[case2 deadline-fires-first] dataCancelledAt+1150ms=" + dataCancelled.get()
                + " itemsStillGrowing(count)=" + count.get());
    }

    static void case3_downstreamCancelsEarly() throws Exception {
        AtomicBoolean dataCancelled = new AtomicBoolean(false);
        AtomicBoolean timerCancelled = new AtomicBoolean(false);
        Flux<Integer> data = Flux.interval(Duration.ofMillis(1000)).map(Long::intValue)
                .doOnCancel(() -> dataCancelled.set(true));
        Mono<Integer> deadlineSignal = Mono.<Integer>delay(Duration.ofSeconds(5))
                .doOnCancel(() -> timerCancelled.set(true))
                .flatMap(tick -> Mono.error(new TimeoutException("absolute-deadline-exceeded")));
        Flux<Integer> merged = data.takeUntilOther(deadlineSignal);

        Disposable subscription = merged.subscribe(v -> {}, e -> {}, () -> {});
        Thread.sleep(150);
        subscription.dispose();
        Thread.sleep(150);
        System.out.println("[case3 downstream-cancel] dataCancelled=" + dataCancelled.get()
                + " timerCancelled=" + timerCancelled.get());
    }

    static void case4_upstreamErrorsFirst() throws Exception {
        AtomicBoolean timerCancelled = new AtomicBoolean(false);
        Flux<Integer> data = Flux.<Integer>error(new IllegalStateException("simulated-upstream-error"))
                .delaySubscription(Duration.ofMillis(50));
        Mono<Integer> deadlineSignal = Mono.<Integer>delay(Duration.ofSeconds(5))
                .doOnCancel(() -> timerCancelled.set(true))
                .flatMap(tick -> Mono.error(new TimeoutException("absolute-deadline-exceeded")));
        Flux<Integer> merged = data.takeUntilOther(deadlineSignal);

        CountDownLatch latch = new CountDownLatch(1);
        final Throwable[] err = new Throwable[1];
        merged.subscribe(v -> {}, e -> { err[0] = e; latch.countDown(); }, latch::countDown);
        latch.await(2, TimeUnit.SECONDS);
        Thread.sleep(150);
        System.out.println("[case4 upstream-error-first] error=" + (err[0] == null ? "none" : err[0].getClass().getSimpleName() + ":" + err[0].getMessage())
                + " timerCancelled=" + timerCancelled.get());
    }
}
