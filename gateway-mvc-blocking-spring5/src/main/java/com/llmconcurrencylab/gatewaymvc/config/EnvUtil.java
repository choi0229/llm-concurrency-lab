package com.llmconcurrencylab.gatewaymvc.config;

/** Ported unchanged from Phase 1/2 (gateway-mvc-java21/.../config/EnvUtil.java). */
public final class EnvUtil {

    private EnvUtil() {
    }

    public static String getString(String name, String defaultValue) {
        String value = System.getenv(name);
        return (value == null || value.isEmpty()) ? defaultValue : value;
    }

    public static int getInt(String name, int defaultValue) {
        String value = System.getenv(name);
        if (value == null || value.isEmpty()) {
            return defaultValue;
        }
        return Integer.parseInt(value.trim());
    }
}
