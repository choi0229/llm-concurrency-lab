package com.llmconcurrencylab.gatewaymvc.upstream;

import java.io.IOException;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

import com.llmconcurrencylab.gatewaymvc.config.EnvUtil;
import org.springframework.stereotype.Component;

/**
 * Deliberately uses only java.net.HttpURLConnection (no Apache/OkHttp/etc.) —
 * this is the Baseline's whole point: reproduce the synchronous-blocking
 * outbound call that the real chatbot executor threads made.
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
     * @param remainingMs the caller's own absolute-deadline budget (see
     *     docs/decisions/timeout-semantics.md) — connect/read timeouts are
     *     clamped to this so a request that's already used up most of its
     *     total budget in queue wait doesn't get the FULL configured
     *     connect/read timeout on top of that.
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
