package com.llmconcurrencylab.phase4.webflux;

/**
 * Not byte-shared with M1/M2's EnvUtil (docs/test-plan/phase4-design.md section 43 — M3 requires
 * semantic parity, not source parity). Identical behavior.
 */
public final class EnvUtil {

    private EnvUtil() {
    }

    public static String getString(String name, String defaultValue) {
        String v = System.getenv(name);
        return (v == null || v.isEmpty()) ? defaultValue : v;
    }

    public static int getInt(String name, int defaultValue) {
        String v = System.getenv(name);
        return (v == null || v.isEmpty()) ? defaultValue : Integer.parseInt(v.trim());
    }
}
