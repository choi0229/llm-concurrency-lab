package com.llmconcurrencylab.gatewaymvc.deadline;

import java.time.Duration;

import org.junit.jupiter.api.Test;
import reactor.core.publisher.Flux;
import reactor.test.StepVerifier;

/**
 * Unit 4 §28 case D / §29: minimal Reactor-level backpressure checks. Not a claim that this proves
 * WebClient's own HTTP-layer buffering behaves correctly under a real slow network client — that's
 * Unit 8 scope (Unit 4 §29 explicitly disclaims that). This only verifies the {@link
 * AbsoluteDeadline} operator itself doesn't secretly depend on downstream demand to function.
 */
class BackpressureAndDemandTest {

    @Test
    void deadlineFiresEvenWithZeroDownstreamDemandEverGranted() {
        // Upstream that would run forever if left alone; downstream here requests nothing at all.
        Flux<Integer> upstream = Flux.never();
        Flux<Integer> withDeadline = AbsoluteDeadline.apply(upstream, Duration.ofMillis(200));

        StepVerifier.create(withDeadline, 0) // zero initial demand, and nothing ever requested
                .expectSubscription()
                .expectErrorMatches(e -> e instanceof AbsoluteDeadlineExceededException)
                .verify(Duration.ofSeconds(2));
    }

    @Test
    void cancellationPropagatesEvenWithZeroDownstreamDemandEverGranted() {
        java.util.concurrent.atomic.AtomicBoolean upstreamCancelled = new java.util.concurrent.atomic.AtomicBoolean(false);
        Flux<Integer> upstream = Flux.<Integer>never().doOnCancel(() -> upstreamCancelled.set(true));
        Flux<Integer> withDeadline = AbsoluteDeadline.apply(upstream, Duration.ofSeconds(5));

        StepVerifier.create(withDeadline, 0) // zero demand for the whole subscription lifetime
                .expectSubscription()
                .thenAwait(Duration.ofMillis(100))
                .thenCancel()
                .verify(Duration.ofSeconds(2));

        org.assertj.core.api.Assertions.assertThat(upstreamCancelled.get()).isTrue();
    }
}
