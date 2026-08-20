package com.llmconcurrencylab.gatewaymvc.executor;

import java.util.concurrent.Executors;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.SynchronousQueue;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import com.llmconcurrencylab.gatewaymvc.config.EnvUtil;
import com.llmconcurrencylab.gatewaymvc.metrics.ExecutorStateCollector;
import com.llmconcurrencylab.gatewaymvc.metrics.GatewayMetrics;
import io.prometheus.client.CollectorRegistry;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * THREAD_MODE selects both which chat executor bean is active AND which
 * ChatTaskSubmitter implements admission/instrumentation for it — see
 * docs/decisions/phase2-module-structure.md and docs/test-plan/
 * phase2-design.md section 2-1 (Unit 3 note 6). The two @Bean methods below
 * both call ThreadMode.fromEnv() rather than sharing one parsed value via a
 * field, so each bean's wiring stays independently traceable from its own
 * method — a deliberate small duplication over introducing shared mutable
 * config state.
 */
@Configuration
public class ChatExecutorConfig {

    // Matches P-E's max pool size exactly — Unit 3's primary comparison
    // requires the same chat task / admission concurrency ceiling on both
    // sides (docs/test-plan/phase2-design.md section 2-1 Primary, Unit 3
    // section 1). Not "outbound concurrency" specifically — the permit is
    // held for the whole task lifecycle, not just the HttpURLConnection
    // call (see VirtualTaskInstrumentedRunnable's Javadoc).
    private static final int VT_LIMITED_PERMITS_DEFAULT = 50;

    @Bean(destroyMethod = "shutdown")
    public ExecutorService chatExecutor() {
        switch (ThreadMode.fromEnv()) {
            case PLATFORM:
                return platformExecutor();
            case VIRTUAL_LIMITED:
            case VIRTUAL_UNLIMITED:
                // Same raw executor for both VT modes — the only difference
                // between them is admission control, which lives entirely in
                // the ChatTaskSubmitter (VirtualLimitedTaskSubmitter's
                // Semaphore vs VirtualUnlimitedTaskSubmitter's absence of one).
                return Executors.newVirtualThreadPerTaskExecutor();
            default:
                throw new AssertionError("unreachable — ThreadMode.fromEnv() already validates");
        }
    }

    @Bean
    public ChatTaskSubmitter chatTaskSubmitter(ExecutorService chatExecutor, GatewayMetrics metrics) {
        switch (ThreadMode.fromEnv()) {
            case PLATFORM:
                return new PlatformTaskSubmitter(chatExecutor, metrics);
            case VIRTUAL_LIMITED:
                int permits = EnvUtil.getInt("CHAT_VT_LIMITED_PERMITS", VT_LIMITED_PERMITS_DEFAULT);
                return new VirtualLimitedTaskSubmitter(chatExecutor, metrics, permits);
            case VIRTUAL_UNLIMITED:
                return new VirtualUnlimitedTaskSubmitter(chatExecutor, metrics);
            default:
                throw new AssertionError("unreachable — ThreadMode.fromEnv() already validates");
        }
    }

    /**
     * P-E: Phase 1 Config E ported to Java 21 (docs/test-plan/
     * phase2-design.md section 2-1) — core=10, max=50, SynchronousQueue,
     * AbortPolicy, keepAlive=60s (all unchanged from Phase 1's Config E).
     * Queue type and rejection policy are hardcoded rather than
     * environment-switchable (unlike Phase 1's ChatExecutorConfig, which had
     * to support B/D/E/A from one class) — SynchronousQueue+AbortPolicy is
     * what defines P-E's identity in Phase 2, not a variable to tune per run.
     * Only the pool-size/keepAlive *values* are env-configurable, so they
     * stay inspectable/recorded in environment.json without risking an
     * accidental queue-type misconfiguration mid-benchmark.
     */
    private ThreadPoolExecutor platformExecutor() {
        int corePoolSize = EnvUtil.getInt("CHAT_CORE_POOL_SIZE", 10);
        int maxPoolSize = EnvUtil.getInt("CHAT_MAX_POOL_SIZE", 50);
        int keepAliveSeconds = EnvUtil.getInt("CHAT_KEEP_ALIVE_SECONDS", 60);

        ThreadPoolExecutor executor = new ThreadPoolExecutor(
                corePoolSize, maxPoolSize, keepAliveSeconds, TimeUnit.SECONDS,
                new SynchronousQueue<Runnable>(),
                chatThreadFactory(),
                new ThreadPoolExecutor.AbortPolicy());

        CollectorRegistry.defaultRegistry.register(new ExecutorStateCollector(executor));
        return executor;
    }

    private ThreadFactory chatThreadFactory() {
        return new ThreadFactory() {
            private final AtomicInteger counter = new AtomicInteger(1);

            @Override
            public Thread newThread(Runnable r) {
                Thread thread = new Thread(r, "chat-executor-" + counter.getAndIncrement());
                thread.setDaemon(true);
                return thread;
            }
        };
    }
}
