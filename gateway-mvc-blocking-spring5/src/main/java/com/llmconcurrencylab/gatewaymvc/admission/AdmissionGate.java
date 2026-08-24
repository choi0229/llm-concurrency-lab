package com.llmconcurrencylab.gatewaymvc.admission;

import java.util.concurrent.Semaphore;

import com.llmconcurrencylab.gatewaymvc.config.EnvUtil;
import org.springframework.stereotype.Component;

/**
 * Application-level admission ceiling (docs/decisions/phase3-admission-connection-pool.md §1).
 * tryAcquire() only — never the blocking acquire() overload, never a wait queue. A request either
 * gets a permit immediately or is rejected immediately; no in-between state.
 *
 * Non-fair (default Semaphore ordering) — same as Phase 2's VirtualLimitedTaskSubmitter; fairness
 * is not a Phase 3 experiment variable either.
 */
@Component
public class AdmissionGate {

    public static final int DEFAULT_LIMIT = 50;

    private final Semaphore semaphore;
    private final int limit;

    public AdmissionGate() {
        this(EnvUtil.getInt("CHAT_ADMISSION_LIMIT", DEFAULT_LIMIT));
    }

    /** Explicit-limit constructor — used by tests; Spring wiring always goes through {@link #AdmissionGate()}. */
    public AdmissionGate(int limit) {
        this.limit = limit;
        this.semaphore = new Semaphore(limit, false);
    }

    public boolean tryAcquire() {
        return semaphore.tryAcquire();
    }

    public void release() {
        semaphore.release();
    }

    public int limit() {
        return limit;
    }

    /** Diagnostic only (Unit 1 ADR §1-2 note — not a Prometheus metric, smoke-test assertion use). */
    public int availablePermits() {
        return semaphore.availablePermits();
    }
}
