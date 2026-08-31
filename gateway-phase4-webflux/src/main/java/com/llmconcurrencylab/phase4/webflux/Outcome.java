package com.llmconcurrencylab.phase4.webflux;

/**
 * Same label set as M1/M2 (docs/decisions/phase4-metrics-contract.md section 3) for
 * cross-model comparability. WRITE_OVERFLOW is retained in the enum for label-set parity but is
 * never produced by M3 — there is no application write queue here (docs/test-plan/
 * phase4-design.md section 7); no code path assigns it.
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
