package com.llmconcurrencylab.phase4.platformqueue;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

import com.llmconcurrencylab.phase4.common.DeadlineWatchdog;
import com.llmconcurrencylab.phase4.common.Outcome;
import com.llmconcurrencylab.phase4.common.RequestLifecycle;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;

/**
 * Unit 2 F5/F6 (docs/test-plan/phase4-design.md section 4, Unit 2 sections 10-11) — test-only
 * tiny executor (worker=1, queue=1), no real network, no 550-concurrency load.
 *
 * <p>Note on why this is a plain unit test rather than an HTTP-level functional test: with a
 * single shared CHAT_TOTAL_TIMEOUT_MS applied identically from each request's own admission time,
 * a request admitted earlier always reaches its own deadline no later than one admitted after it
 * — so a real occupying request can never legitimately hold the worker past a later request's
 * deadline via the shared deadline mechanism alone. Testing the true race (a queued task's
 * deadline firing while the worker is still occupied by unrelated, controlled work) requires
 * decoupling worker-occupation duration from the deadline constant, which only a direct unit test
 * can do deterministically.
 */
class PlatformTaskSubmitterTest {

    @Test
    void thirdRequestIsRejectedWhenWorkerAndQueueAreBothFull() throws Exception {
        ThreadPoolExecutor executor = new ThreadPoolExecutor(
                1, 1, 0L, TimeUnit.SECONDS, new ArrayBlockingQueue<>(1, false));
        PlatformExecutorMetrics metrics = new PlatformExecutorMetrics(new SimpleMeterRegistry(), executor);
        PlatformTaskSubmitter submitter = new PlatformTaskSubmitter(executor, metrics);

        CountDownLatch workerRunning = new CountDownLatch(1);
        CountDownLatch releaseWorker = new CountDownLatch(1);
        RequestLifecycle lifecycle1 = new RequestLifecycle("t1", 60000);
        assertTrue(submitter.trySubmit(lifecycle1, () -> {
            workerRunning.countDown();
            await(releaseWorker);
        }));
        assertTrue(workerRunning.await(2, TimeUnit.SECONDS));

        RequestLifecycle lifecycle2 = new RequestLifecycle("t2", 60000);
        assertTrue(submitter.trySubmit(lifecycle2, () -> {
        }), "second request should fill the single queue slot");

        RequestLifecycle lifecycle3 = new RequestLifecycle("t3", 60000);
        boolean thirdAccepted = submitter.trySubmit(lifecycle3, () -> {
        });
        assertFalse(thirdAccepted, "worker(1) + queue(1) both full -> third submission must be rejected");

        releaseWorker.countDown();
        executor.shutdown();
        assertTrue(executor.awaitTermination(5, TimeUnit.SECONDS));
    }

    @Test
    void queuedTaskThatTimesOutNeverRunsAndIsRemovedFromQueue() throws Exception {
        ThreadPoolExecutor executor = new ThreadPoolExecutor(
                1, 1, 0L, TimeUnit.SECONDS, new ArrayBlockingQueue<>(1, false));
        PlatformExecutorMetrics metrics = new PlatformExecutorMetrics(new SimpleMeterRegistry(), executor);
        PlatformTaskSubmitter submitter = new PlatformTaskSubmitter(executor, metrics);
        DeadlineWatchdog watchdog = new DeadlineWatchdog();

        // Occupy the single worker with a task under full test control — NOT tied to any
        // RequestLifecycle deadline, so its duration is fully decoupled from request #2's own
        // deadline (see class-level note on why this must be a direct unit test).
        CountDownLatch workerRunning = new CountDownLatch(1);
        CountDownLatch releaseWorker = new CountDownLatch(1);
        executor.execute(() -> {
            workerRunning.countDown();
            await(releaseWorker);
        });
        assertTrue(workerRunning.await(2, TimeUnit.SECONDS));

        RequestLifecycle lifecycle = new RequestLifecycle("t2", 300);
        AtomicBoolean upstreamCalled = new AtomicBoolean(false);
        Runnable task = () -> {
            if (lifecycle.isTerminal()) {
                return;
            }
            upstreamCalled.set(true);
        };
        assertTrue(submitter.trySubmit(lifecycle, task), "should be accepted into the single queue slot");
        assertEquals(1, executor.getQueue().size());

        AtomicBoolean deadlineCallbackRan = new AtomicBoolean(false);
        watchdog.arm(lifecycle, lc -> deadlineCallbackRan.set(true));

        Thread.sleep(600); // well past the 300ms deadline; worker is still held by releaseWorker

        assertTrue(lifecycle.isTerminal());
        assertEquals(Outcome.TIMEOUT, lifecycle.terminalOutcomeOrNull());
        assertTrue(deadlineCallbackRan.get(), "watchdog callback must have run");
        assertFalse(upstreamCalled.get(),
                "no phantom upstream: a queued task must not start once its deadline has fired");
        assertEquals(0, executor.getQueue().size(), "timed-out task must be removed from the queue");

        releaseWorker.countDown();
        executor.shutdown();
        assertTrue(executor.awaitTermination(5, TimeUnit.SECONDS));
    }

    private static void await(CountDownLatch latch) {
        try {
            latch.await(10, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }
}
