package com.llmconcurrencylab.gatewaymvc.executor;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Semaphore;

import com.llmconcurrencylab.gatewaymvc.metrics.GatewayMetrics;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * THREAD_MODE=VIRTUAL_LIMITED submission path (docs/test-plan/
 * phase2-design.md section 2-1) — admission is gated by a fixed-permit
 * Semaphore BEFORE any virtual thread is created, matching P-E's chat task /
 * admission concurrency ceiling exactly for the primary comparison. Called
 * "admission" rather than "outbound" deliberately — the permit protects the
 * whole task lifecycle, not just the HttpURLConnection call (see
 * VirtualTaskInstrumentedRunnable's Javadoc, Unit 3.5 clarification).
 *
 * Admission uses ONLY Semaphore.tryAcquire() (non-blocking) — never
 * acquire()/acquireUninterruptibly(), and there is no permit-waiting queue.
 * A request either gets a permit immediately or is rejected immediately,
 * mirroring P-E's SynchronousQueue+AbortPolicy semantics (accept-or-reject,
 * never wait) — see docs/test-plan/phase2-design.md Unit 3 section 1.
 *
 * gateway_request_outcome_total{outcome="rejected"} is the common source of
 * truth for admission rejection across BOTH P-E and VT-Limited (Unit 3
 * section 2) — this class's own chat_virtual_admission_rejected_total is a
 * VT-only diagnostic counter, not a cross-mode comparison metric.
 */
public class VirtualLimitedTaskSubmitter implements ChatTaskSubmitter {

    private static final Logger log = LoggerFactory.getLogger(VirtualLimitedTaskSubmitter.class);

    private final ExecutorService chatExecutor;
    private final GatewayMetrics metrics;
    private final Semaphore semaphore;

    public VirtualLimitedTaskSubmitter(ExecutorService chatExecutor, GatewayMetrics metrics, int permits) {
        this.chatExecutor = chatExecutor;
        this.metrics = metrics;
        // Non-fair (default Semaphore ordering) — Phase 2 does not treat
        // Semaphore fairness as an experiment variable (Unit 3 section 1).
        this.semaphore = new Semaphore(permits, false);
    }

    @Override
    public boolean trySubmit(Runnable work) {
        if (!semaphore.tryAcquire()) {
            metrics.virtualAdmissionRejectedTotal.inc();
            return false;
        }
        try {
            chatExecutor.execute(new VirtualTaskInstrumentedRunnable(work, metrics, semaphore));
            return true;
        } catch (RuntimeException e) {
            // Submission itself failed (e.g. executor shut down) before the
            // virtual thread ever started — VirtualTaskInstrumentedRunnable's
            // finally therefore never runs, so THIS is the only other place
            // the permit can be released. Without this catch, a failed
            // submission would leak a permit permanently (Unit 3 pre-fix 0-4).
            log.error("chat task submission failed after acquiring an admission permit — releasing it directly", e);
            semaphore.release();
            metrics.virtualAdmissionRejectedTotal.inc();
            return false;
        }
    }

    /**
     * Diagnostic only — NOT a formal Prometheus metric (docs/test-plan/
     * phase2-design.md Unit 3 section 9: "Prometheus formal metric으로 만들
     * 필요는 없지만 smoke test assertion으로 확인한다").
     */
    public int availablePermits() {
        return semaphore.availablePermits();
    }
}
