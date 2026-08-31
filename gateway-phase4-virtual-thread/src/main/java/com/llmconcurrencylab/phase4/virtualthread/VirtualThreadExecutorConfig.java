package com.llmconcurrencylab.phase4.virtualthread;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * M2 — VT outbound executor. No application admission ceiling, no bounded task queue, no
 * artificial Semaphore (docs/test-plan/phase4-design.md section 5). Every request gets its own
 * virtual thread task, unconditionally.
 */
@Configuration
public class VirtualThreadExecutorConfig {

    @Bean(destroyMethod = "shutdown")
    public ExecutorService virtualOutboundExecutor() {
        return Executors.newVirtualThreadPerTaskExecutor();
    }
}
