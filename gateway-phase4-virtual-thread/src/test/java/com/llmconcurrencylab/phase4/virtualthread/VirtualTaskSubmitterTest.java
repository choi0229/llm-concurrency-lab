package com.llmconcurrencylab.phase4.virtualthread;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

import com.llmconcurrencylab.phase4.common.RequestLifecycle;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;

/**
 * Unit 2 F9 (docs/test-plan/phase4-design.md section 5, Unit 2 sections 14/36) plus M2 metric
 * cleanup (Unit 2 section 47).
 */
class VirtualTaskSubmitterTest {

    @Test
    void taskRunsOnARealVirtualThreadAndMetricsAreCleanedUpAfterCompletion() throws Exception {
        ExecutorService virtualExecutor = Executors.newVirtualThreadPerTaskExecutor();
        VirtualTaskMetrics metrics = new VirtualTaskMetrics(new SimpleMeterRegistry());
        VirtualTaskSubmitter submitter = new VirtualTaskSubmitter(virtualExecutor, metrics);

        RequestLifecycle lifecycle = new RequestLifecycle("t1", 60000);
        AtomicBoolean isVirtual = new AtomicBoolean(false);
        AtomicBoolean activeDuringRun = new AtomicBoolean(false);
        CountDownLatch done = new CountDownLatch(1);

        boolean accepted = submitter.trySubmit(lifecycle, () -> {
            isVirtual.set(Thread.currentThread().isVirtual());
            activeDuringRun.set(metrics.activeValue() == 1);
            done.countDown();
        });

        assertTrue(accepted, "VirtualTaskSubmitter never rejects");
        assertTrue(done.await(2, TimeUnit.SECONDS));
        // metrics.activeValue() is decremented in a finally{} that races this assertion, so poll.
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(2);
        while (metrics.activeValue() != 0 && System.nanoTime() < deadline) {
            Thread.sleep(10);
        }

        assertTrue(isVirtual.get(), "task must run on a real virtual thread (Thread.isVirtual()==true)");
        assertTrue(activeDuringRun.get(), "virtual.tasks.active must be 1 while the task is running");
        assertEquals(0, metrics.activeValue(), "virtual.tasks.active must return to 0 after completion");

        virtualExecutor.shutdown();
        assertTrue(virtualExecutor.awaitTermination(5, TimeUnit.SECONDS));
    }
}
