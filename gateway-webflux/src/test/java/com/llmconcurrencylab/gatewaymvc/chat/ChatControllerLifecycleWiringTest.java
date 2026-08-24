package com.llmconcurrencylab.gatewaymvc.chat;

import java.io.IOException;
import java.time.Duration;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

import com.llmconcurrencylab.gatewaymvc.admission.AdmissionGate;
import com.llmconcurrencylab.gatewaymvc.deadline.AbsoluteDeadline;
import com.llmconcurrencylab.gatewaymvc.metrics.GatewayMetrics;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;
import org.springframework.http.codec.ServerSentEvent;
import reactor.core.Disposable;
import reactor.core.publisher.Flux;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Exercises {@link ChatController#attachLifecycle} directly against fake upstream Fluxes — the
 * required race matrix from Unit 4 §27/§28, without a Spring context or real WebClient/Netty.
 */
class ChatControllerLifecycleWiringTest {

    private static ServerSentEvent<String> sse(String event, String data) {
        return ServerSentEvent.<String>builder().event(event).data(data).build();
    }

    private RequestLifecycle newLifecycle(GatewayMetrics metrics) {
        return new RequestLifecycle("req-1", new AdmissionGate(5), metrics, System.nanoTime());
    }

    @Test
    void normalCompleteRecordsCompletedOutcome() {
        GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
        RequestLifecycle lifecycle = newLifecycle(metrics);
        Flux<ServerSentEvent<String>> upstream = Flux.just(sse("delta", "1"), sse("delta", "2"), sse("final", "done"));

        Flux<ServerSentEvent<String>> wired = ChatController.attachLifecycle(upstream, lifecycle, metrics, "req-1");
        java.util.List<ServerSentEvent<String>> received = wired.collectList().block(Duration.ofSeconds(2));

        assertThat(received).hasSize(3);
        assertThat(lifecycle.outcome()).isEqualTo("completed");
        assertThat(metrics.upstreamActiveValue()).isZero();
    }

    @Test
    void deadlineExceededRecordsTimeoutOutcome() {
        GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
        RequestLifecycle lifecycle = newLifecycle(metrics);
        // Never emits, never completes on its own -- only the deadline can end it.
        Flux<ServerSentEvent<String>> upstream = AbsoluteDeadline.apply(Flux.never(), Duration.ofMillis(200));

        Flux<ServerSentEvent<String>> wired = ChatController.attachLifecycle(upstream, lifecycle, metrics, "req-1");
        wired.onErrorResume(e -> Flux.empty()).blockLast(Duration.ofSeconds(2));

        assertThat(lifecycle.outcome()).isEqualTo("timeout");
        assertThat(metrics.upstreamActiveValue()).isZero();
    }

    @Test
    void upstreamErrorRecordsUpstreamErrorOutcome() {
        GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
        RequestLifecycle lifecycle = newLifecycle(metrics);
        Flux<ServerSentEvent<String>> upstream = Flux.error(new IOException("simulated upstream failure"));

        Flux<ServerSentEvent<String>> wired = ChatController.attachLifecycle(upstream, lifecycle, metrics, "req-1");
        wired.onErrorResume(e -> reactor.core.publisher.Mono.empty()).blockLast(Duration.ofSeconds(2));

        assertThat(lifecycle.outcome()).isEqualTo("upstream_error");
        assertThat(metrics.upstreamActiveValue()).isZero();
    }

    @Test
    void downstreamCancelRecordsClientDisconnectOutcome() throws Exception {
        GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
        RequestLifecycle lifecycle = newLifecycle(metrics);
        Flux<ServerSentEvent<String>> upstream = Flux.interval(Duration.ofMillis(50))
                .map(i -> sse("delta", String.valueOf(i)));

        Flux<ServerSentEvent<String>> wired = ChatController.attachLifecycle(upstream, lifecycle, metrics, "req-1");
        Disposable subscription = wired.subscribe(v -> {
        }, e -> {
        }, () -> {
        });
        Thread.sleep(150);
        subscription.dispose();
        Thread.sleep(150);

        assertThat(lifecycle.outcome()).isEqualTo("client_disconnect");
        assertThat(metrics.upstreamActiveValue()).isZero();
    }

    @Test
    void deadlineVsUpstreamErrorRace_exactlyOneOutcomeEitherIsAcceptable() throws Exception {
        // Data errors at ~roughly the same time the deadline would fire -- whichever wins, the
        // CAS in RequestLifecycle must still land on exactly one outcome (Unit 4 §27/§28).
        for (int i = 0; i < 10; i++) {
            GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
            RequestLifecycle lifecycle = newLifecycle(metrics);
            Flux<ServerSentEvent<String>> flaky = Flux.<ServerSentEvent<String>>error(new IOException("late failure"))
                    .delaySubscription(Duration.ofMillis(150));
            Flux<ServerSentEvent<String>> upstream = AbsoluteDeadline.apply(flaky, Duration.ofMillis(150));

            Flux<ServerSentEvent<String>> wired = ChatController.attachLifecycle(upstream, lifecycle, metrics, "req-1");
            CountDownLatch done = new CountDownLatch(1);
            wired.subscribe(v -> {
            }, e -> done.countDown(), done::countDown);
            assertThat(done.await(3, TimeUnit.SECONDS)).isTrue();
            Thread.sleep(50);

            assertThat(lifecycle.outcome()).isIn("timeout", "upstream_error");
            assertThat(metrics.upstreamActiveValue()).isZero();
        }
    }

    @Test
    void deadlineVsDownstreamCancelRace_exactlyOneOutcomeEitherIsAcceptable() throws Exception {
        for (int i = 0; i < 10; i++) {
            GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
            RequestLifecycle lifecycle = newLifecycle(metrics);
            Flux<ServerSentEvent<String>> upstream = AbsoluteDeadline.apply(Flux.never(), Duration.ofMillis(150));

            Flux<ServerSentEvent<String>> wired = ChatController.attachLifecycle(upstream, lifecycle, metrics, "req-1");
            Disposable subscription = wired.subscribe(v -> {
            }, e -> {
            }, () -> {
            });
            Thread.sleep(150); // dispose right around when the deadline is scheduled to fire
            subscription.dispose();
            Thread.sleep(150);

            assertThat(lifecycle.outcome()).isIn("timeout", "client_disconnect");
            assertThat(metrics.upstreamActiveValue()).isZero();
        }
    }

    @Test
    void firstChunkAndBytesRelayedMetricsRecorded() {
        SimpleMeterRegistry registry = new SimpleMeterRegistry();
        GatewayMetrics metrics = new GatewayMetrics(registry);
        RequestLifecycle lifecycle = newLifecycle(metrics);
        Flux<ServerSentEvent<String>> upstream = Flux.just(sse("delta", "abc"), sse("final", "done"));

        ChatController.attachLifecycle(upstream, lifecycle, metrics, "req-1").blockLast(Duration.ofSeconds(2));

        assertThat(registry.get("gateway.first.chunk.relay").timer().count()).isEqualTo(1);
        assertThat(registry.get("gateway.bytes.relayed").counter().count()).isGreaterThan(0);
    }
}
