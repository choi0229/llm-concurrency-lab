package com.llmconcurrencylab.phase4.common;

/**
 * Common terminal outcome set (docs/decisions/phase4-metrics-contract.md section 3,
 * docs/test-plan/phase4-design.md section 7 — write_overflow does not occur on M3, which has no
 * application write queue; the label set is still shared for cross-model comparability).
 */
public enum Outcome {
    COMPLETED("completed"),
    REJECTED("rejected"),
    TIMEOUT("timeout"),
    UPSTREAM_ERROR("upstream_error"),
    CLIENT_DISCONNECT("client_disconnect"),
    INTERNAL_ERROR("internal_error"),
    WRITE_OVERFLOW("write_overflow");

    private final String label;

    Outcome(String label) {
        this.label = label;
    }

    public String label() {
        return label;
    }
}
