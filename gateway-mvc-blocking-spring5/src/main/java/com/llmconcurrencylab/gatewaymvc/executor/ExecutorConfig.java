package com.llmconcurrencylab.gatewaymvc.executor;

import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.SynchronousQueue;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import com.llmconcurrencylab.gatewaymvc.admission.AdmissionGate;
import com.llmconcurrencylab.gatewaymvc.config.EnvUtil;
import com.llmconcurrencylab.gatewaymvc.metrics.GatewayMetrics;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * The two executors this module uses are deliberately separate resources
 * (docs/decisions/phase3-mvc-webclient-write-path.md §2, Unit 2 §2 "Blocking outbound executor와
 * Servlet write executor는 완전히 다른 resource다") plus a small shared scheduler for the absolute
 * deadline watchdog (docs/decisions/phase3-timeout-cancellation.md §9, Unit 2 §9 "P3-A는 shared
 * deadline scheduler/watchdog를 사용한다").
 */
@Configuration
public class ExecutorConfig {

    // "functional default — not formal frozen" (Unit 2 §11/§26): a real number is needed to run,
    // but Formal sizing for these two pools is out of Unit 2's scope
    // (docs/decisions/phase3-mvc-webclient-write-path.md §5-2, phase3-admission-connection-pool.md
    // §3-5 — "Unit 5 functional screening 전 확정, Formal 시작 후 변경 금지").
    private static final int SERVLET_WRITE_POOL_SIZE_DEFAULT = 8;
    private static final int SERVLET_WRITE_QUEUE_CAPACITY_DEFAULT = 64;

    /**
     * P3-A blocking outbound executor (docs/decisions/phase3-admission-connection-pool.md §2):
     * core=max=admission ceiling, SynchronousQueue, AbortPolicy — "thread-first, no wait queue",
     * no hidden queue behind application admission. Default pool size tracks the admission ceiling
     * unless CHAT_BLOCKING_POOL_SIZE explicitly overrides it (Unit 2 §7's "Formal에서는 admission
     * ceiling과 동일하게 맞추는 것을 우선 정책으로 둔다").
     */
    @Bean(destroyMethod = "shutdown")
    public ThreadPoolExecutor blockingOutboundExecutor(AdmissionGate admissionGate, GatewayMetrics metrics) {
        int poolSize = EnvUtil.getInt("CHAT_BLOCKING_POOL_SIZE", admissionGate.limit());
        ThreadPoolExecutor executor = new ThreadPoolExecutor(
                poolSize, poolSize, 60, TimeUnit.SECONDS,
                new SynchronousQueue<Runnable>(),
                daemonThreadFactory("blocking-outbound-"),
                new ThreadPoolExecutor.AbortPolicy());
        metrics.bindBlockingOutboundExecutor(executor);
        return executor;
    }

    /**
     * Shared bounded Servlet write executor (docs/decisions/phase3-mvc-webclient-write-path.md
     * §5) — P3-A and (later) P3-B use the identical class/config here, only the producer side
     * differs. Bounded ArrayBlockingQueue (never unbounded) + AbortPolicy (fail fast, never
     * caller-runs/blocking).
     */
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
     * Shared absolute-deadline watchdog scheduler. One delayed task per request, cancelled by
     * RequestLifecycle.tryTerminate() on any other terminal path (docs/decisions/
     * phase3-timeout-cancellation.md §1/§4). Small fixed pool — each task does a cheap CAS +
     * cleanup, never blocking work.
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
