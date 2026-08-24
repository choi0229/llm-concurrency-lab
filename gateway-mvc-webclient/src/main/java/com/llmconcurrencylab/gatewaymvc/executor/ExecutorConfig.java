package com.llmconcurrencylab.gatewaymvc.executor;

import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import com.llmconcurrencylab.gatewaymvc.config.EnvUtil;
import com.llmconcurrencylab.gatewaymvc.metrics.GatewayMetrics;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Ported from P3-A's ExecutorConfig with one deliberate omission (Unit 3 §1/§22): no blocking
 * outbound executor bean — P3-B has no blocking outbound call to run on a dedicated Platform
 * Thread pool, WebClient's Reactor Netty event loop replaces that role entirely. The Servlet write
 * executor and the deadline watchdog scheduler are otherwise identical to P3-A's (docs/decisions/
 * phase3-mvc-webclient-write-path.md §5, docs/decisions/phase3-timeout-cancellation.md §9).
 */
@Configuration
public class ExecutorConfig {

    // "functional default — not formal frozen" (Unit 2 §11/§26, carried over unchanged).
    private static final int SERVLET_WRITE_POOL_SIZE_DEFAULT = 8;
    private static final int SERVLET_WRITE_QUEUE_CAPACITY_DEFAULT = 64;

    @Bean(destroyMethod = "shutdown")
    public ThreadPoolExecutor servletWriteExecutor(GatewayMetrics metrics) {
        int poolSize = EnvUtil.getInt("SERVLET_WRITE_POOL_SIZE", SERVLET_WRITE_POOL_SIZE_DEFAULT);
        int queueCapacity = EnvUtil.getInt("SERVLET_WRITE_QUEUE_CAPACITY", SERVLET_WRITE_QUEUE_CAPACITY_DEFAULT);
        ThreadPoolExecutor executor = new ThreadPoolExecutor(
                poolSize, poolSize, 60, TimeUnit.SECONDS,
                new ArrayBlockingQueue<Runnable>(queueCapacity),
                daemonThreadFactory("servlet-write-"),
                new ThreadPoolExecutor.AbortPolicy());
        metrics.bindServletWriteExecutor(executor);
        return executor;
    }

    /**
     * Shared absolute-deadline watchdog scheduler — the SAME authoritative mechanism as P3-A
     * (Unit 3 §9: "P3-A와 동일한 의미의 shared ScheduledExecutorService + request absolute
     * deadline task를 P3-B에서도 유지하는 방식을 우선한다"). One delayed task per request,
     * cancelled by RequestLifecycle.tryTerminate() on any other terminal path.
     */
    @Bean(destroyMethod = "shutdown")
    public ScheduledExecutorService deadlineWatchdogExecutor() {
        return Executors.newScheduledThreadPool(2, daemonThreadFactory("deadline-watchdog-"));
    }

    private static ThreadFactory daemonThreadFactory(String prefix) {
        return new ThreadFactory() {
            private final AtomicInteger counter = new AtomicInteger(1);

            @Override
            public java.lang.Thread newThread(Runnable r) {
                java.lang.Thread thread = new java.lang.Thread(r, prefix + counter.getAndIncrement());
                thread.setDaemon(true);
                return thread;
            }
        };
    }
}
