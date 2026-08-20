package com.llmconcurrencylab.gatewaymvc.executor;

/**
 * Mode-aware task submission — the one seam ChatController defers to for
 * THREAD_MODE-specific admission control + instrumentation, so
 * ChatController itself stays free of THREAD_MODE conditionals
 * (docs/test-plan/phase2-design.md section 2-1, Unit 3 note 6: "Controller
 * 전체에 조건문이 퍼지지 않게 하되... 과도한 추상화를 새로 만들지 않는다" —
 * this is a single method, not a general framework).
 */
public interface ChatTaskSubmitter {

    /**
     * @return true if work was submitted for execution (caller does nothing
     *     further — the submitter's wrapper owns recording the request's
     *     eventual outcome); false if admission was rejected, in which case
     *     the caller is responsible for the uniform outcome="rejected" /
     *     HTTP 503 handling (identical response shape regardless of which
     *     THREAD_MODE rejected it).
     */
    boolean trySubmit(Runnable work);
}
