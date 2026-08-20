package com.llmconcurrencylab.gatewaymvc.executor;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.RejectedExecutionException;

import com.llmconcurrencylab.gatewaymvc.metrics.GatewayMetrics;

/**
 * THREAD_MODE=PLATFORM (P-E) submission path — wraps with
 * InstrumentedRunnable (records executor_task_start_delay_seconds{mode=
 * "pool"}) and lets ThreadPoolExecutor's own admission control (AbortPolicy)
 * decide accept/reject synchronously inside execute().
 * gateway_executor_rejected_total is P-E's real ThreadPoolExecutor rejection
 * counter — never incremented by VirtualLimitedTaskSubmitter's Semaphore
 * admission rejection (docs/test-plan/phase2-design.md section 2-1, Unit 3
 * section 2).
 */
public class PlatformTaskSubmitter implements ChatTaskSubmitter {

    private final ExecutorService chatExecutor;
    private final GatewayMetrics metrics;

    public PlatformTaskSubmitter(ExecutorService chatExecutor, GatewayMetrics metrics) {
        this.chatExecutor = chatExecutor;
        this.metrics = metrics;
    }

    @Override
    public boolean trySubmit(Runnable work) {
        try {
            chatExecutor.execute(new InstrumentedRunnable(work, metrics));
            return true;
        } catch (RejectedExecutionException e) {
            metrics.executorRejectedTotal.inc();
            return false;
        }
    }
}
