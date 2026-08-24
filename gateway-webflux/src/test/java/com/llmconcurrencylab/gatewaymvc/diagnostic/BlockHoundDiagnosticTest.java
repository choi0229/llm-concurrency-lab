package com.llmconcurrencylab.gatewaymvc.diagnostic;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import reactor.blockhound.BlockHound;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Unit 5.5 D1 diagnostic — P3-C counterpart of gateway-mvc-webclient's
 * {@code BlockHoundDiagnosticTest}; see that class's javadoc for the full rationale (identical
 * here). NOT part of the default {@code ./gradlew test} suite — run via
 * {@code ./gradlew blockhoundDiagnostic}. Requires a real Mock LLM already running at
 * {@code MOCK_LLM_BASE_URL} (default {@code http://127.0.0.1:8000}).
 *
 * <p>Exercises the full production path: real {@code AdmissionGate}, real
 * {@code WebClientConfig} (including the dedicated {@code gateway-webclient-loop}
 * {@code LoopResources}), real {@code ChatController} functional route, real Reactor Netty
 * server. If any Reactor Netty event-loop thread (server or client) performed a blocking call
 * while handling this request, BlockHound is expected to catch it.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@Tag("blockhound-diagnostic")
class BlockHoundDiagnosticTest {

    private static final java.util.concurrent.atomic.AtomicReference<Throwable> UNCAUGHT =
            new java.util.concurrent.atomic.AtomicReference<>();

    static {
        BlockHound.install();
        Thread.setDefaultUncaughtExceptionHandler((thread, throwable) -> {
            UNCAUGHT.compareAndSet(null, throwable);
            System.err.println("UNCAUGHT_ON[" + thread.getName() + "]: " + throwable);
            throwable.printStackTrace();
        });
    }

    @LocalServerPort
    private int port;

    @Test
    void normalSseRequestProducesNoBlockHoundViolation() throws Exception {
        String requestBody = "{\"firstChunkDelayMs\":300,\"chunkIntervalMs\":150,\"chunkCount\":5,\"chunkSizeBytes\":16}";
        URL url = new URL("http://127.0.0.1:" + port + "/chat/stream");
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        connection.setRequestMethod("POST");
        connection.setRequestProperty("Content-Type", "application/json");
        connection.setDoOutput(true);
        byte[] bodyBytes = requestBody.getBytes(StandardCharsets.UTF_8);
        connection.setFixedLengthStreamingMode(bodyBytes.length);
        try (OutputStream out = connection.getOutputStream()) {
            out.write(bodyBytes);
        }

        assertEquals(200, connection.getResponseCode(), "Normal SSE request must succeed (200)");

        int deltaCount = 0;
        boolean sawFinal = false;
        try (BufferedReader reader =
                new BufferedReader(new InputStreamReader(connection.getInputStream(), StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                if (line.startsWith("event: delta") || line.startsWith("event:delta")) {
                    deltaCount++;
                } else if (line.startsWith("event: final") || line.startsWith("event:final")) {
                    sawFinal = true;
                }
            }
        }
        connection.disconnect();

        Thread.sleep(500);

        assertEquals(5, deltaCount, "Expected all 5 delta events -- a truncated stream can itself be a symptom "
                + "of a blocking violation having broken the Reactor chain mid-stream");
        assertTrue(sawFinal, "Expected the final event -- see above");

        Throwable uncaught = UNCAUGHT.get();
        if (uncaught != null) {
            System.out.println("BLOCKHOUND_RESULT=VIOLATION_DETECTED: " + uncaught);
        } else {
            System.out.println("BLOCKHOUND_RESULT=NO_VIOLATION_OBSERVED");
        }
        assertTrue(uncaught == null || !(uncaught instanceof reactor.blockhound.BlockingOperationError),
                "BlockHound detected a blocking call on a non-blocking thread during this request: " + uncaught);
    }
}
