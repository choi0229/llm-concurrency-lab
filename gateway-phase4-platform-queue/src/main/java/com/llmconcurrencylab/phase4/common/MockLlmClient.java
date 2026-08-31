package com.llmconcurrencylab.phase4.common;

import java.io.IOException;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

import org.springframework.stereotype.Component;

/**
 * Deliberately uses only java.net.HttpURLConnection (no Apache/OkHttp/etc., no
 * java.net.http.HttpClient, no WebClient) — the M1 vs M2 comparison keeps outbound identical so
 * the only variable is the chat task's thread model (docs/test-plan/phase4-design.md section 3).
 * Same remaining-budget-clamp pattern validated in Phase 1/2/3 (docs/decisions/
 * phase3-timeout-cancellation.md section 4).
 */
@Component
public class MockLlmClient {

    private final String baseUrl;
    private final int connectTimeoutMs;
    private final int readTimeoutMs;

    public MockLlmClient() {
        this.baseUrl = EnvUtil.getString("MOCK_LLM_BASE_URL", "http://127.0.0.1:8000");
        this.connectTimeoutMs = EnvUtil.getInt("CHAT_CONNECT_TIMEOUT_MS", 3000);
        this.readTimeoutMs = EnvUtil.getInt("CHAT_READ_TIMEOUT_MS", 30000);
    }

    /** Test-only: explicit values instead of env vars, so unit tests can point at a local fixture
     * HTTP server without process-wide environment mutation. Not used by production wiring. */
    MockLlmClient(String baseUrl, int connectTimeoutMs, int readTimeoutMs) {
        this.baseUrl = baseUrl;
        this.connectTimeoutMs = connectTimeoutMs;
        this.readTimeoutMs = readTimeoutMs;
    }

    /**
     * Caller owns the connection: read getInputStream(), then always disconnect().
     *
     * @param remainingMs the caller's own absolute-deadline budget — connect/read timeouts are
     *     clamped to this so a request that already used up most of its total budget (e.g. M1
     *     queue wait) doesn't get the FULL configured connect/read timeout on top of that.
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
