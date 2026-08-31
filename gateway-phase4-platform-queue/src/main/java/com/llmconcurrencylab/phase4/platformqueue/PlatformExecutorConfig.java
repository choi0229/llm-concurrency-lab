package com.llmconcurrencylab.phase4.platformqueue;

import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import com.llmconcurrencylab.phase4.common.EnvUtil;

/**
 * M1 — PT-QUEUE outbound executor. Exact construction frozen in docs/test-plan/phase4-design.md
 * section 0-1 / section 4: core=max=50 (fixed), ArrayBlockingQueue(500, fairness=false),
 * AbortPolicy, lazy core-thread start (prestartAllCoreThreads not called).
 *
 * <p>Worker count / queue capacity are env-overridable (docs/test-plan/phase4-design.md /
 * Unit 2 section 48 "Production Parameterization") ONLY so functional tests (F5/F6) can exercise
 * a tiny test-only executor (e.g. worker=1, queue=1) without a 550-concurrency load. The Formal
 * harness MUST NOT override these — defaults are frozen at 50/500 and Formal always uses the
 * defaults.
 */
@Configuration
public class PlatformExecutorConfig {

    @Bean(destroyMethod = "shutdown")
    public ThreadPoolExecutor platformOutboundExecutor() {
        int workerCount = EnvUtil.getInt("PT_WORKER_COUNT", 50);
        int queueCapacity = EnvUtil.getInt("PT_QUEUE_CAPACITY", 500);
        AtomicInteger counter = new AtomicInteger();
        ThreadFactory threadFactory = r -> {
            Thread t = new Thread(r, "phase4-pt-outbound-" + counter.incrementAndGet());
            t.setDaemon(true);
            return t;
        };
        return new ThreadPoolExecutor(
                workerCount, workerCount, 0L, TimeUnit.SECONDS,
                new ArrayBlockingQueue<>(queueCapacity, false),
                threadFactory,
                new ThreadPoolExecutor.AbortPolicy());
    }
}
