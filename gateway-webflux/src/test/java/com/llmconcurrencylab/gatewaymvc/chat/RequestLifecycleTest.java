package com.llmconcurrencylab.gatewaymvc.chat;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import com.llmconcurrencylab.gatewaymvc.admission.AdmissionGate;
import com.llmconcurrencylab.gatewaymvc.metrics.GatewayMetrics;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * P3-C's RequestLifecycle has no external resources to race-close (no AsyncContext, no
 * Disposable, no deadline Future — see the class javadoc for why), so this test is a strict
 * subset of P3-A/B's RequestLifecycleTest: only the terminal CAS / outcome-metric / permit
 * exactly-once guarantees apply here.
 */
class RequestLifecycleTest {

    private RequestLifecycle newLifecycle(AdmissionGate gate, GatewayMetrics metrics) {
        return new RequestLifecycle("req-1", gate, metrics, System.nanoTime());
    }

    @Test
    void terminalTransitionSucceedsExactlyOnce() {
        AdmissionGate gate = new AdmissionGate(5);
        GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
        RequestLifecycle lifecycle = newLifecycle(gate, metrics);

        assertThat(lifecycle.tryTerminate("completed")).isTrue();
        assertThat(lifecycle.tryTerminate("timeout")).isFalse();
        assertThat(lifecycle.tryTerminate("upstream_error")).isFalse();
        assertThat(lifecycle.outcome()).isEqualTo("completed");
    }

    @Test
    void outcomeMetricIncrementsExactlyOnceUnderConcurrentRace() throws Exception {
        AdmissionGate gate = new AdmissionGate(5);
        SimpleMeterRegistry registry = new SimpleMeterRegistry();
        GatewayMetrics metrics = new GatewayMetrics(registry);
        RequestLifecycle lifecycle = newLifecycle(gate, metrics);

        String[] outcomes = {"completed", "timeout", "upstream_error", "client_disconnect"};
        int contenders = outcomes.length * 20;
        ExecutorService pool = Executors.newFixedThreadPool(contenders);
        CountDownLatch start = new CountDownLatch(1);
        CountDownLatch done = new CountDownLatch(contenders);
        AtomicInteger winners = new AtomicInteger(0);
        for (int i = 0; i < contenders; i++) {
            String outcome = outcomes[i % outcomes.length];
            pool.submit(() -> {
                try {
                    start.await();
                    if (lifecycle.tryTerminate(outcome)) {
                        winners.incrementAndGet();
                    }
                } catch (InterruptedException ignored) {
                } finally {
                    done.countDown();
                }
            });
        }
        start.countDown();
        assertThat(done.await(5, TimeUnit.SECONDS)).isTrue();
        pool.shutdownNow();

        assertThat(winners.get()).isEqualTo(1);
        double totalOutcomeCount = 0;
        for (String outcome : outcomes) {
            io.micrometer.core.instrument.Counter counter =
                    registry.find("gateway.requests").tag("outcome", outcome).counter();
            if (counter != null) {
                totalOutcomeCount += counter.count();
            }
        }
        assertThat(totalOutcomeCount).isEqualTo(1.0);
    }

    @Test
    void permitReleasedExactlyOnceEvenUnderConcurrentTerminateRace() throws Exception {
        AdmissionGate gate = new AdmissionGate(3);
        GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
        RequestLifecycle lifecycle = newLifecycle(gate, metrics);

        assertThat(gate.tryAcquire()).isTrue();
        lifecycle.markAdmitted();
        assertThat(gate.availablePermits()).isEqualTo(2);

        int contenders = 20;
        ExecutorService pool = Executors.newFixedThreadPool(contenders);
        CountDownLatch start = new CountDownLatch(1);
        CountDownLatch done = new CountDownLatch(contenders);
        for (int i = 0; i < contenders; i++) {
            pool.submit(() -> {
                try {
                    start.await();
                    lifecycle.tryTerminate("completed");
                } catch (InterruptedException ignored) {
                } finally {
                    done.countDown();
                }
            });
        }
        start.countDown();
        assertThat(done.await(5, TimeUnit.SECONDS)).isTrue();
        pool.shutdownNow();

        assertThat(gate.availablePermits()).isEqualTo(3);
    }

    @Test
    void deadlineFiringWinsWhenItIsFirst_upstreamErrorRaceLoses() {
        AdmissionGate gate = new AdmissionGate(5);
        GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
        RequestLifecycle lifecycle = newLifecycle(gate, metrics);

        assertThat(lifecycle.tryTerminate("timeout")).isTrue();
        assertThat(lifecycle.tryTerminate("upstream_error")).isFalse();
        assertThat(lifecycle.outcome()).isEqualTo("timeout");
    }

    @Test
    void deadlineFiringWinsWhenItIsFirst_clientDisconnectRaceLoses() {
        AdmissionGate gate = new AdmissionGate(5);
        GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
        RequestLifecycle lifecycle = newLifecycle(gate, metrics);

        assertThat(lifecycle.tryTerminate("timeout")).isTrue();
        assertThat(lifecycle.tryTerminate("client_disconnect")).isFalse();
        assertThat(lifecycle.outcome()).isEqualTo("timeout");
    }

    @Test
    void firstChunkRecordedOnlyOnce() throws Exception {
        AdmissionGate gate = new AdmissionGate(5);
        SimpleMeterRegistry registry = new SimpleMeterRegistry();
        GatewayMetrics metrics = new GatewayMetrics(registry);
        RequestLifecycle lifecycle = newLifecycle(gate, metrics);

        int contenders = 20;
        ExecutorService pool = Executors.newFixedThreadPool(contenders);
        CountDownLatch start = new CountDownLatch(1);
        CountDownLatch done = new CountDownLatch(contenders);
        for (int i = 0; i < contenders; i++) {
            pool.submit(() -> {
                try {
                    start.await();
                    lifecycle.recordFirstChunkIfNeeded();
                } catch (InterruptedException ignored) {
                } finally {
                    done.countDown();
                }
            });
        }
        start.countDown();
        assertThat(done.await(5, TimeUnit.SECONDS)).isTrue();
        pool.shutdownNow();

        assertThat(registry.get("gateway.first.chunk.relay").timer().count()).isEqualTo(1);
    }
}
