package com.llmconcurrencylab.phase4.virtualthread;

import java.util.concurrent.ExecutorService;

import com.llmconcurrencylab.phase4.common.ChatTaskSubmitter;
import com.llmconcurrencylab.phase4.common.RequestLifecycle;
import org.springframework.stereotype.Component;

/**
 * M2's {@link ChatTaskSubmitter}: always accepts (never rejects — no bounded resource here,
 * docs/test-plan/phase4-design.md section 5). No cancel hook is registered — a
 * {@code VirtualThreadPerTaskExecutor} task starts essentially immediately, there is no queue to
 * remove it from.
 */
@Component
public class VirtualTaskSubmitter implements ChatTaskSubmitter {

    private final ExecutorService executor;
    private final VirtualTaskMetrics metrics;

    public VirtualTaskSubmitter(ExecutorService virtualOutboundExecutor, VirtualTaskMetrics metrics) {
        this.executor = virtualOutboundExecutor;
        this.metrics = metrics;
    }

    @Override
    public boolean trySubmit(RequestLifecycle lifecycle, Runnable task) {
        metrics.taskStarted();
        executor.execute(() -> {
            try {
                task.run();
            } finally {
                metrics.taskEnded();
            }
        });
        return true;
    }
}
