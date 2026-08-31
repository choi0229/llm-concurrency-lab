package com.llmconcurrencylab.phase4.common;

import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Shared Servlet write executor — byte-identical construction in M1 and M2 (docs/test-plan/
 * phase4-design.md section 0-2 / section 6). Bounded ArrayBlockingQueue + AbortPolicy, same
 * rationale as the M1 outbound executor (section 0-1): non-fair queue (JDK default), lazy core
 * thread start (ThreadPoolExecutor default), fixed core==max so keep-alive is inert.
 */
@Configuration
public class WriteExecutorConfig {

    /**
     * Env-overridable ONLY so functional tests (F8, write-overflow) can use a tiny queue without
     * a load. Formal harness MUST NOT override — defaults frozen at 64/20000 (docs/test-plan/
     * phase4-design.md section 0-2 / Unit 2 section 48).
     */
    @Bean(destroyMethod = "shutdown")
    public ThreadPoolExecutor writeExecutor() {
        int poolSize = EnvUtil.getInt("SERVLET_WRITE_POOL_SIZE", 64);
        int queueCapacity = EnvUtil.getInt("SERVLET_WRITE_QUEUE_CAPACITY", 20000);
        AtomicInteger counter = new AtomicInteger();
        ThreadFactory threadFactory = r -> {
            Thread t = new Thread(r, "phase4-write-" + counter.incrementAndGet());
            t.setDaemon(true);
            return t;
        };
        return new ThreadPoolExecutor(
                poolSize, poolSize, 0L, TimeUnit.SECONDS,
                new ArrayBlockingQueue<>(queueCapacity, false),
                threadFactory,
                new ThreadPoolExecutor.AbortPolicy());
    }
}
