package com.llmconcurrencylab.gatewaymvc.deadline;

import java.time.Duration;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

import org.junit.jupiter.api.Test;
import reactor.core.Disposable;
import reactor.core.publisher.Flux;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Formalizes Unit 1's verified candidate C spike (docs/decisions/phase3-timeout-cancellation.md
 * §3-3, docs/test-results/phase3/unit1-capability-spikes/DeadlineSpike-C-final-verified.java) as
 * permanent regression tests against the actual production class, plus the two candidates that
 * were tried and rejected there are NOT reproduced here — this only tests the one that shipped.
 */
class AbsoluteDeadlineTest {

    @Test
    void normalCompletionBeforeDeadlineIsNotReportedAsTimeout() throws Exception {
        Flux<Integer> data = Flux.just(1, 2, 3).delayElements(Duration.ofMillis(20));
        Flux<Integer> withDeadline = AbsoluteDeadline.apply(data, Duration.ofMillis(500));

        CountDownLatch latch = new CountDownLatch(1);
        AtomicInteger count = new AtomicInteger(0);
        AtomicBoolean sawError = new AtomicBoolean(false);
        long t0 = System.nanoTime();
        withDeadline.subscribe(v -> count.incrementAndGet(), e -> {
            sawError.set(true);
            latch.countDown();
        }, latch::countDown);

        assertThat(latch.await(2, TimeUnit.SECONDS)).isTrue();
        long elapsedMs = (System.nanoTime() - t0) / 1_000_000;

        assertThat(count.get()).isEqualTo(3);
        assertThat(sawError.get()).isFalse();
        assertThat(elapsedMs).isLessThan(500); // must not wait for the deadline timer to also expire
    }

    @Test
    void deadlineFiresBeforeUpstreamCompletion_cancelsUpstreamWithNoLeak() throws Exception {
        AtomicInteger rawTicks = new AtomicInteger(0);
        Flux<Integer> data = Flux.interval(Duration.ofMillis(100))
                .doOnNext(t -> rawTicks.incrementAndGet())
                .map(Long::intValue);
        Flux<Integer> withDeadline = AbsoluteDeadline.apply(data, Duration.ofMillis(300));

        CountDownLatch latch = new CountDownLatch(1);
        AtomicInteger errorType = new AtomicInteger(0);
        withDeadline.subscribe(v -> {
        }, e -> {
            if (e instanceof AbsoluteDeadlineExceededException) {
                errorType.incrementAndGet();
            }
            latch.countDown();
        }, latch::countDown);

        assertThat(latch.await(2, TimeUnit.SECONDS)).isTrue();
        assertThat(errorType.get()).isEqualTo(1);

        int ticksAtTermination = rawTicks.get();
        Thread.sleep(1500);
        assertThat(rawTicks.get())
                .as("upstream interval must be cancelled at the deadline, not keep ticking forever")
                .isEqualTo(ticksAtTermination);
    }

    @Test
    void downstreamCancelDisposesUpstreamAndTimer() throws Exception {
        AtomicBoolean dataCancelled = new AtomicBoolean(false);
        Flux<Integer> data = Flux.interval(Duration.ofSeconds(1)).map(Long::intValue)
                .doOnCancel(() -> dataCancelled.set(true));
        Flux<Integer> withDeadline = AbsoluteDeadline.apply(data, Duration.ofSeconds(5));

        Disposable subscription = withDeadline.subscribe(v -> {
        }, e -> {
        }, () -> {
        });
        Thread.sleep(150);
        subscription.dispose();
        Thread.sleep(150);

        assertThat(dataCancelled.get()).isTrue();
    }

    @Test
    void upstreamErrorBeforeDeadlinePropagatesUnchanged() throws Exception {
        Flux<Integer> data = Flux.<Integer>error(new IllegalStateException("simulated-upstream-error"))
                .delaySubscription(Duration.ofMillis(50));
        Flux<Integer> withDeadline = AbsoluteDeadline.apply(data, Duration.ofSeconds(5));

        CountDownLatch latch = new CountDownLatch(1);
        Throwable[] captured = new Throwable[1];
        withDeadline.subscribe(v -> {
        }, e -> {
            captured[0] = e;
            latch.countDown();
        }, latch::countDown);

        assertThat(latch.await(2, TimeUnit.SECONDS)).isTrue();
        assertThat(captured[0]).isInstanceOf(IllegalStateException.class).hasMessage("simulated-upstream-error");
    }
}
