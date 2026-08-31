package com.llmconcurrencylab.phase4.common;

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

    public static long getLong(String name, long defaultValue) {
        String v = System.getenv(name);
        return (v == null || v.isEmpty()) ? defaultValue : Long.parseLong(v.trim());
    }
}
