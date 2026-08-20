package com.llmconcurrencylab.gatewaymvc.executor;

import java.util.concurrent.ExecutorService;

import com.llmconcurrencylab.gatewaymvc.metrics.GatewayMetrics;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * THREAD_MODE=VIRTUAL_UNLIMITED submission path (Unit 4, docs/test-plan/
 * phase2-design.md section 2-2 Secondary) — no admission gate at all. Every
 * accepted request gets a virtual thread task immediately; there is no
 * Semaphore, no permit, no ceiling. Reuses VirtualTaskInstrumentedRunnable
 * (same chat_virtual_tasks_* metrics as VIRTUAL_LIMITED) with `semaphore=
 * null`, so nothing is released in its finally block — there is nothing to
 * release.
 *
 * chat_virtual_admission_rejected_total is NEVER incremented here — that
 * counter means "Semaphore.tryAcquire() failed", which cannot happen when
 * there is no Semaphore. The only rejection path here is
 * chatExecutor.execute() itself throwing (e.g. the executor was shut down
 * mid-request) — an operational failure, not an admission-capacity decision
 * — handled via the same uniform ChatTaskSubmitter.trySubmit()=false / HTTP
 * 503 / outcome="rejected" contract as the other two submitters, for
 * exactly-once accounting consistency (docs/decisions/
 * request-outcome-accounting.md) — not to be confused with
 * VirtualLimitedTaskSubmitter's Semaphore-based admission rejection.
 */
public class VirtualUnlimitedTaskSubmitter implements ChatTaskSubmitter {

    private static final Logger log = LoggerFactory.getLogger(VirtualUnlimitedTaskSubmitter.class);

    private final ExecutorService chatExecutor;
    private final GatewayMetrics metrics;

    public VirtualUnlimitedTaskSubmitter(ExecutorService chatExecutor, GatewayMetrics metrics) {
        this.chatExecutor = chatExecutor;
        this.metrics = metrics;
    }

    @Override
    public boolean trySubmit(Runnable work) {
        try {
            chatExecutor.execute(new VirtualTaskInstrumentedRunnable(work, metrics, null));
            return true;
        } catch (RuntimeException e) {
            // Task submission itself failed — not admission-capacity
            // rejection (there is no capacity limit under VIRTUAL_UNLIMITED).
            log.error("chat task submission failed under VIRTUAL_UNLIMITED (unexpected)", e);
            return false;
        }
    }
}
