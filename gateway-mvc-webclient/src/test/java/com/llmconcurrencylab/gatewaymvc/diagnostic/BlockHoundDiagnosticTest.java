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
 * Unit 5.5 D1 diagnostic — NOT part of the default {@code ./gradlew test} suite (see build.gradle:
 * excluded from `test`, run via `./gradlew blockhoundDiagnostic`). Boots the REAL Spring context
 * (real {@code AdmissionGate}, real {@code WebClientConfig}, real {@code ChatController}) with
 * BlockHound installed, fires one real Normal-SSE request against a real, already-running Mock LLM
 * (must be reachable at {@code MOCK_LLM_BASE_URL}, default {@code http://127.0.0.1:8000} —
 * external precondition of this diagnostic, not started by the test itself), and checks whether
 * any {@code BlockingOperationError} surfaced anywhere in the JVM during that request.
 *
 * Detection is intentionally redundant across two independent signals, since BlockHound
 * violations on a Reactor Netty event-loop thread do not reliably propagate back to this test's
 * own call stack (the violating call happens on a different thread than the one making the HTTP
 * request): (1) a process-wide default {@link Thread.UncaughtExceptionHandler} that records if
 * invoked, and (2) the response itself completing normally (a violation on the response path
 * would very likely break the stream, e.g. an incomplete/short response).
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@Tag("blockhound-diagnostic")
class BlockHoundDiagnosticTest {

    private static final java.util.concurrent.atomic.AtomicReference<Throwable> UNCAUGHT =
            new java.util.concurrent.atomic.AtomicReference<>();

    static {
        // Installed at class-load time -- guaranteed to run before Spring context creation
        // (before any WebClient/ConnectionProvider/LoopResources bean, hence before any Reactor
        // Netty event-loop thread exists) rather than relying on JUnit/Spring lifecycle ordering.
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

        // Give any async violation on a different thread a moment to reach the uncaught handler
        // before we check it.
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
