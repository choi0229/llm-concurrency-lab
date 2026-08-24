package com.llmconcurrencylab.gatewaymvc.executor;

import java.util.concurrent.BlockingQueue;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.RejectedExecutionHandler;
import java.util.concurrent.SynchronousQueue;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import com.llmconcurrencylab.gatewaymvc.config.EnvUtil;
import com.llmconcurrencylab.gatewaymvc.metrics.ExecutorStateCollector;
import io.prometheus.client.CollectorRegistry;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class ChatExecutorConfig {

    @Bean(destroyMethod = "shutdown")
    public ThreadPoolExecutor chatExecutor() {
        int corePoolSize = EnvUtil.getInt("CHAT_CORE_POOL_SIZE", 10);
        int maxPoolSize = EnvUtil.getInt("CHAT_MAX_POOL_SIZE", 10);
        int queueCapacity = EnvUtil.getInt("CHAT_QUEUE_CAPACITY", 100);
        String rejectionPolicy = EnvUtil.getString("CHAT_REJECTION_POLICY", "ABORT");

        BlockingQueue<Runnable> queue = queueCapacity <= 0
                ? new SynchronousQueue<Runnable>()
                : new LinkedBlockingQueue<Runnable>(queueCapacity);

        RejectedExecutionHandler handler = "CALLER_RUNS".equalsIgnoreCase(rejectionPolicy)
                ? new TaggingCallerRunsPolicy()
                : new ThreadPoolExecutor.AbortPolicy();

        ThreadPoolExecutor executor = new ThreadPoolExecutor(
                corePoolSize, maxPoolSize, 60L, TimeUnit.SECONDS, queue, chatThreadFactory(), handler);

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
