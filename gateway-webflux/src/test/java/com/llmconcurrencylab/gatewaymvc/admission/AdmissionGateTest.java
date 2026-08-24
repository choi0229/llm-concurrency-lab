package com.llmconcurrencylab.gatewaymvc.admission;

import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class AdmissionGateTest {

    @Test
    void limitExceededIsRejectedImmediately() {
        AdmissionGate gate = new AdmissionGate(1);

        assertThat(gate.tryAcquire()).isTrue();
        // second concurrent request must be rejected immediately, not wait — tryAcquire() is
        // non-blocking by construction (java.util.concurrent.Semaphore#tryAcquire()), this
        // asserts the observable contract rather than just re-testing the JDK.
        assertThat(gate.tryAcquire()).isFalse();

        gate.release();
        assertThat(gate.tryAcquire()).isTrue();
    }

    @Test
    void neverBlocksUnderConcurrentContentionAtTheCeiling() throws Exception {
        int limit = 10;
        AdmissionGate gate = new AdmissionGate(limit);
        for (int i = 0; i < limit; i++) {
            assertThat(gate.tryAcquire()).isTrue();
        }

        int contenders = 50;
        ExecutorService pool = Executors.newFixedThreadPool(contenders);
        CountDownLatch start = new CountDownLatch(1);
        CountDownLatch done = new CountDownLatch(contenders);
        CopyOnWriteArrayList<Boolean> results = new CopyOnWriteArrayList<>();
        for (int i = 0; i < contenders; i++) {
            pool.submit(() -> {
                try {
                    start.await();
                    results.add(gate.tryAcquire());
                } catch (InterruptedException ignored) {
                } finally {
                    done.countDown();
                }
            });
        }
        start.countDown();
        boolean finished = done.await(5, TimeUnit.SECONDS);
        pool.shutdownNow();

        assertThat(finished).as("all tryAcquire() calls must return promptly, never block").isTrue();
        assertThat(results).allMatch(accepted -> !accepted);
        assertThat(gate.availablePermits()).isZero();
    }
}
