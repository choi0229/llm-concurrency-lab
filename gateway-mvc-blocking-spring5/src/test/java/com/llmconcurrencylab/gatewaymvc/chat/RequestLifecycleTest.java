package com.llmconcurrencylab.gatewaymvc.chat;

import java.net.HttpURLConnection;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import javax.servlet.AsyncContext;

import com.llmconcurrencylab.gatewaymvc.admission.AdmissionGate;
import com.llmconcurrencylab.gatewaymvc.metrics.GatewayMetrics;
import com.llmconcurrencylab.gatewaymvc.write.PerStreamWriteChannel;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyBoolean;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class RequestLifecycleTest {

    private RequestLifecycle newLifecycle(AsyncContext asyncContext, AdmissionGate gate, GatewayMetrics metrics) {
        return new RequestLifecycle("req-1", asyncContext, gate, metrics, System.nanoTime());
    }

    @Test
    void terminalTransitionSucceedsExactlyOnce() {
        AsyncContext ctx = mock(AsyncContext.class);
        AdmissionGate gate = new AdmissionGate(5);
        GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
        RequestLifecycle lifecycle = newLifecycle(ctx, gate, metrics);

        assertThat(lifecycle.tryTerminate("completed")).isTrue();
        assertThat(lifecycle.tryTerminate("timeout")).isFalse();
        assertThat(lifecycle.tryTerminate("upstream_error")).isFalse();
        assertThat(lifecycle.outcome()).isEqualTo("completed");
        verify(ctx, times(1)).complete();
    }

    @Test
    void outcomeMetricIncrementsExactlyOnceUnderConcurrentRace() throws Exception {
        AsyncContext ctx = mock(AsyncContext.class);
        AdmissionGate gate = new AdmissionGate(5);
        SimpleMeterRegistry registry = new SimpleMeterRegistry();
        GatewayMetrics metrics = new GatewayMetrics(registry);
        RequestLifecycle lifecycle = newLifecycle(ctx, gate, metrics);

        String[] outcomes = {"completed", "timeout", "upstream_error", "client_disconnect", "internal_error"};
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
        AsyncContext ctx = mock(AsyncContext.class);
        AdmissionGate gate = new AdmissionGate(3);
        GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
        RequestLifecycle lifecycle = newLifecycle(ctx, gate, metrics);

        assertThat(gate.tryAcquire()).isTrue(); // this request's permit
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

        assertThat(gate.availablePermits()).isEqualTo(3); // exactly one release, not 20
    }

    @Test
    void upstreamConnectionDisconnectedExactlyOnce() {
        AsyncContext ctx = mock(AsyncContext.class);
        AdmissionGate gate = new AdmissionGate(5);
        GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
        RequestLifecycle lifecycle = newLifecycle(ctx, gate, metrics);

        HttpURLConnection connection = mock(HttpURLConnection.class);
        lifecycle.setConnection(connection);

        lifecycle.tryTerminate("timeout");
        lifecycle.tryTerminate("client_disconnect"); // loses the race, must not disconnect again

        verify(connection, times(1)).disconnect();
    }

    @Test
    void asyncContextCompletedExactlyOnceAndIllegalStateExceptionIsSwallowed() {
        AsyncContext ctx = mock(AsyncContext.class);
        org.mockito.Mockito.doThrow(new IllegalStateException("already completed")).when(ctx).complete();
        AdmissionGate gate = new AdmissionGate(5);
        GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
        RequestLifecycle lifecycle = newLifecycle(ctx, gate, metrics);

        // Must not throw even though asyncContext.complete() itself throws.
        assertThat(lifecycle.tryTerminate("client_disconnect")).isTrue();
        verify(ctx, times(1)).complete();
    }

    @Test
    void deadlineTaskCancelledWhenAnotherOutcomeWinsFirst() {
        AsyncContext ctx = mock(AsyncContext.class);
        AdmissionGate gate = new AdmissionGate(5);
        GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
        RequestLifecycle lifecycle = newLifecycle(ctx, gate, metrics);

        @SuppressWarnings("unchecked")
        ScheduledFuture<Object> deadlineTask = mock(ScheduledFuture.class);
        lifecycle.setDeadlineTask(deadlineTask);

        lifecycle.tryTerminate("completed"); // e.g. "normal completion before deadline"

        verify(deadlineTask, times(1)).cancel(false);
    }

    @Test
    void deadlineFiringWinsWhenItIsFirst_upstreamErrorRaceLoses() {
        AsyncContext ctx = mock(AsyncContext.class);
        AdmissionGate gate = new AdmissionGate(5);
        GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
        RequestLifecycle lifecycle = newLifecycle(ctx, gate, metrics);

        assertThat(lifecycle.tryTerminate("timeout")).isTrue();
        assertThat(lifecycle.tryTerminate("upstream_error")).isFalse(); // races the deadline, loses
        assertThat(lifecycle.outcome()).isEqualTo("timeout");
    }

    @Test
    void deadlineFiringWinsWhenItIsFirst_clientDisconnectRaceLoses() {
        AsyncContext ctx = mock(AsyncContext.class);
        AdmissionGate gate = new AdmissionGate(5);
        GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
        RequestLifecycle lifecycle = newLifecycle(ctx, gate, metrics);

        assertThat(lifecycle.tryTerminate("timeout")).isTrue();
        assertThat(lifecycle.tryTerminate("client_disconnect")).isFalse();
        assertThat(lifecycle.outcome()).isEqualTo("timeout");
    }

    @Test
    void resourcesRegisteredAfterTerminalAreCleanedUpImmediately() {
        AsyncContext ctx = mock(AsyncContext.class);
        AdmissionGate gate = new AdmissionGate(5);
        GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
        RequestLifecycle lifecycle = newLifecycle(ctx, gate, metrics);

        lifecycle.tryTerminate("timeout"); // terminal before any resource was registered

        HttpURLConnection connection = mock(HttpURLConnection.class);
        lifecycle.setConnection(connection);
        verify(connection, times(1)).disconnect();

        @SuppressWarnings("unchecked")
        Future<Object> outboundFuture = mock(Future.class);
        when(outboundFuture.cancel(anyBoolean())).thenReturn(true);
        lifecycle.setOutboundFuture(outboundFuture);
        verify(outboundFuture, times(1)).cancel(true);

        @SuppressWarnings("unchecked")
        ScheduledFuture<Object> deadlineTask = mock(ScheduledFuture.class);
        lifecycle.setDeadlineTask(deadlineTask);
        verify(deadlineTask, times(1)).cancel(false);

        PerStreamWriteChannel channel = mock(PerStreamWriteChannel.class);
        lifecycle.setWriteChannel(channel);
        verify(channel, times(1)).markTerminal();
    }

    @Test
    void firstChunkRecordedOnlyOnce() throws Exception {
        AsyncContext ctx = mock(AsyncContext.class);
        AdmissionGate gate = new AdmissionGate(5);
        SimpleMeterRegistry registry = new SimpleMeterRegistry();
        GatewayMetrics metrics = new GatewayMetrics(registry);
        RequestLifecycle lifecycle = newLifecycle(ctx, gate, metrics);

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
