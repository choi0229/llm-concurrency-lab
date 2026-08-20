package com.llmconcurrencylab.gatewaymvc.executor;

import com.llmconcurrencylab.gatewaymvc.config.EnvUtil;

/**
 * THREAD_MODE parsed once here so ChatExecutorConfig's two @Bean methods
 * (chatExecutor, chatTaskSubmitter) don't each re-parse/re-validate the raw
 * env var string independently — this is NOT a general abstraction layer,
 * just avoiding duplicated parsing/validation logic between two @Bean
 * methods that both need to agree on the same mode (docs/test-plan/
 * phase2-design.md section 2-1, Unit 3 note 6).
 */
public enum ThreadMode {
    PLATFORM,
    VIRTUAL_LIMITED,
    VIRTUAL_UNLIMITED;

    public static ThreadMode fromEnv() {
        String raw = EnvUtil.getString("THREAD_MODE", "PLATFORM").toUpperCase();
        try {
            return valueOf(raw);
        } catch (IllegalArgumentException e) {
            throw new IllegalStateException("Unknown THREAD_MODE=" + raw
                    + " (expected PLATFORM, VIRTUAL_LIMITED, or VIRTUAL_UNLIMITED)");
        }
    }
}
