package com.llmconcurrencylab.phase4.common;

/**
 * The single seam where M1 (bounded ThreadPoolExecutor) and M2 (VirtualThreadPerTaskExecutor)
 * differ (docs/test-plan/phase4-design.md section 3) — everything else in ChatController is
 * identical between M1 and M2.
 */
public interface ChatTaskSubmitter {

    /** @return true if the task was accepted for execution, false if rejected (M1 only — M2 never rejects). */
    boolean trySubmit(RequestLifecycle lifecycle, Runnable task);
}
