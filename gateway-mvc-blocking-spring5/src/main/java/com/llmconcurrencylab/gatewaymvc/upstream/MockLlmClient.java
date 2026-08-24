package com.llmconcurrencylab.gatewaymvc.upstream;

import java.io.IOException;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

import com.llmconcurrencylab.gatewaymvc.config.EnvUtil;
import org.springframework.stereotype.Component;

/**
 * Ported from Phase 1/2 (gateway-mvc-java21/.../upstream/MockLlmClient.java) unchanged — Unit 1
 * froze this exact request shape (docs/test-plan/phase3-design.md §3-2) and Unit 2 §8 says to
 * reuse it as-is. Deliberately java.net.HttpURLConnection only — this blocking client is the
 * entire point of the P3-A control (docs/decisions/phase3-mvc-webclient-write-path.md §1).
 */
@Component
public class MockLlmClient {

    private final String baseUrl;
    private final int connectTimeoutMs;
    private final int readTimeoutMs;

    public MockLlmClient() {
        this.baseUrl = EnvUtil.getString("MOCK_LLM_BASE_URL", "http://localhost:8000");
        this.connectTimeoutMs = EnvUtil.getInt("CHAT_CONNECT_TIMEOUT_MS", 3000);
        this.readTimeoutMs = EnvUtil.getInt("CHAT_READ_TIMEOUT_MS", 30000);
    }

    /**
     * Caller owns the connection: read getInputStream(), then always disconnect().
     *
     * @param remainingMs the caller's absolute-deadline budget remaining (docs/decisions/
     *     phase3-timeout-cancellation.md §1/§4) — connect/read timeouts are clamped to this so a
     *     request that already used up most of its total budget doesn't get the full configured
     *     connect/read timeout on top of that. This clamp is a lower-level safety timeout, not a
     *     substitute for the absolute deadline watchdog (see ADR §4 and §9 of the Unit 2 brief).
     */
    public HttpURLConnection openStream(String requestBodyJson, long remainingMs) throws IOException {
        URL url = new URL(baseUrl + "/mock/stream");
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        connection.setRequestMethod("POST");
        connection.setRequestProperty("Content-Type", "application/json");
        connection.setRequestProperty("Accept", "text/event-stream");
        connection.setConnectTimeout((int) Math.max(1, Math.min(connectTimeoutMs, remainingMs)));
        connection.setReadTimeout((int) Math.max(1, Math.min(readTimeoutMs, remainingMs)));
        connection.setDoOutput(true);
        connection.setUseCaches(false);

        byte[] body = requestBodyJson.getBytes(StandardCharsets.UTF_8);
        connection.setFixedLengthStreamingMode(body.length);
        OutputStream out = connection.getOutputStream();
        try {
            out.write(body);
            out.flush();
        } finally {
            out.close();
        }
        return connection;
    }
}
