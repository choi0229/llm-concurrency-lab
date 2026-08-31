package com.llmconcurrencylab.phase4.common;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.io.OutputStream;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

import com.sun.net.httpserver.HttpServer;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

/**
 * Unit 5.1 regression: deadline-vs-terminal-CAS race in {@link BlockingMockLlmRelay}. Confirmed
 * real bug (docs/test-results/phase4/unit5-closed-screening-pre-timeout-fix/m1-n400-screen1/): the
 * relay's own {@code isPastDeadline()} check could observe the deadline had passed and {@code
 * break} out of the read loop WITHOUT the lifecycle actually being terminal yet (that only happens
 * once some path — the relay itself, or {@code DeadlineWatchdog}'s async callback — wins the
 * {@code tryTerminate} CAS), and the post-loop code unconditionally treated "not yet terminal" as
 * "producer finished normally," misclassifying a deadline-truncated stream as COMPLETED.
 *
 * <p>No real Mock LLM process or Gateway process is used — a tiny fixture {@link HttpServer}
 * (JDK built-in, no new dependency) stands in for Mock so the race can be reproduced
 * deterministically and fast, independent of any external load/timing.
 */
class BlockingMockLlmRelayDeadlineRaceTest {

    private HttpServer server;

    @AfterEach
    void stopFixtureServer() {
        if (server != null) {
            server.stop(0);
        }
    }

    private int startContinuousStreamServer(int chunkIntervalMs) throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/mock/stream", exchange -> {
            exchange.getRequestBody().readAllBytes();
            exchange.getResponseHeaders().set("Content-Type", "text/event-stream");
            exchange.sendResponseHeaders(200, 0);
            try (OutputStream os = exchange.getResponseBody()) {
                for (int i = 1; i <= 5000; i++) {
                    String frame = "event: delta\ndata: {\"sequence\":" + i + "}\n\n";
                    os.write(frame.getBytes(StandardCharsets.UTF_8));
                    os.flush();
                    Thread.sleep(chunkIntervalMs);
                }
            } catch (Exception ignored) {
                // client disconnected once the test tears the connection down -- expected
            }
        });
        server.start();
        return server.getAddress().getPort();
    }

    private PerStreamWriteChannel buildChannel(RequestLifecycle lifecycle, ThreadPoolExecutor writeExecutor,
            GatewayMetrics metrics, AtomicReference<Outcome> finalOutcome, CountDownLatch terminalLatch) {
        PrintWriter writer = new PrintWriter(new StringWriter());
        WriteChannelMetrics writeMetrics = new WriteChannelMetrics(new SimpleMeterRegistry(), writeExecutor);
        return new PerStreamWriteChannel(lifecycle, writer, writeExecutor, metrics, writeMetrics, outcome -> {
            finalOutcome.set(outcome);
            terminalLatch.countDown();
        });
    }

    /**
     * Test A — isolates the relay's OWN deadline-observation path (the exact code this Unit fixed):
     * no DeadlineWatchdog is armed at all, so the ONLY way the lifecycle can become terminal is via
     * the relay loop's own {@code isPastDeadline()} check. Under the pre-fix code, this test would
     * fail with COMPLETED (nothing else was ever setting terminal, so the buggy fallthrough always
     * fired); confirmed by temporarily reverting the fix and re-running this test during Unit 5.1
     * verification (see Unit 5.1 report).
     */
    @Test
    void deadlineObservedBeforeWatchdogMustNotCompleteStream() throws Exception {
        int port = startContinuousStreamServer(10); // fast chunks, well inside the deadline window
        MockLlmClient client = new MockLlmClient("http://127.0.0.1:" + port, 5000, 5000);
        GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
        BlockingMockLlmRelay relay = new BlockingMockLlmRelay(client, metrics);

        RequestLifecycle lifecycle = new RequestLifecycle("race-relay-first", 150); // no watchdog armed
        ThreadPoolExecutor writeExecutor = new ThreadPoolExecutor(1, 1, 0L, TimeUnit.SECONDS, new ArrayBlockingQueue<>(100));
        AtomicReference<Outcome> finalOutcome = new AtomicReference<>();
        CountDownLatch terminalLatch = new CountDownLatch(1);
        PerStreamWriteChannel channel = buildChannel(lifecycle, writeExecutor, metrics, finalOutcome, terminalLatch);

        relay.relay(lifecycle, "{}", channel);

        assertTrue(terminalLatch.await(5, TimeUnit.SECONDS), "terminal callback must fire");
        assertEquals(Outcome.TIMEOUT, finalOutcome.get(),
                "a stream whose absolute deadline the relay itself observed mid-read must be TIMEOUT, never COMPLETED");
        assertEquals(Outcome.TIMEOUT, lifecycle.terminalOutcomeOrNull());

        writeExecutor.shutdown();
        assertTrue(writeExecutor.awaitTermination(5, TimeUnit.SECONDS));
    }

    /**
     * Test B — simulates a competing path (standing in for DeadlineWatchdog, or any other terminal
     * source) winning the terminal CAS WHILE the relay is still actively reading successfully.
     * Verifies the relay's pre-existing top-of-loop {@code isTerminal()} guard (unchanged by this
     * fix) correctly respects a terminal state set by someone else -- exactly once, never double-
     * counted, never overwritten by a late COMPLETED.
     */
    @Test
    void terminalWonByAnotherPathWhileRelayStillReadingIsRespectedExactlyOnce() throws Exception {
        int port = startContinuousStreamServer(5); // keeps streaming well past our simulated winner
        MockLlmClient client = new MockLlmClient("http://127.0.0.1:" + port, 5000, 5000);
        GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
        BlockingMockLlmRelay relay = new BlockingMockLlmRelay(client, metrics);

        // Deliberately generous lifecycle deadline (300ms) so the relay's OWN isPastDeadline()
        // check does not fire during this short test -- isolates the "someone else already won"
        // path instead of Test A's "relay itself wins" path.
        RequestLifecycle lifecycle = new RequestLifecycle("race-other-first", 300);
        ThreadPoolExecutor writeExecutor = new ThreadPoolExecutor(1, 1, 0L, TimeUnit.SECONDS, new ArrayBlockingQueue<>(100));
        AtomicReference<Outcome> finalOutcome = new AtomicReference<>();
        CountDownLatch terminalLatch = new CountDownLatch(1);
        PerStreamWriteChannel channel = buildChannel(lifecycle, writeExecutor, metrics, finalOutcome, terminalLatch);

        // Simulated competing winner (stands in for DeadlineWatchdog's async callback or a
        // disconnect-detection path), firing while relay is still mid-stream.
        Thread simulatedWatchdog = new Thread(() -> {
            try {
                Thread.sleep(30);
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            }
            if (lifecycle.tryTerminate(Outcome.TIMEOUT)) {
                channel.terminateNow(Outcome.TIMEOUT);
            }
        });
        simulatedWatchdog.start();

        relay.relay(lifecycle, "{}", channel);
        simulatedWatchdog.join(2000);

        assertTrue(terminalLatch.await(5, TimeUnit.SECONDS), "terminal callback must fire exactly once");
        assertEquals(Outcome.TIMEOUT, finalOutcome.get());
        assertEquals(Outcome.TIMEOUT, lifecycle.terminalOutcomeOrNull(),
                "relay must not overwrite a terminal state that another path already won");

        writeExecutor.shutdown();
        assertTrue(writeExecutor.awaitTermination(5, TimeUnit.SECONDS));
    }

    /** Sanity control: with no deadline pressure at all, a stream that reaches "final" normally
     * still completes as COMPLETED -- the fix must not have broken the ordinary path. */
    @Test
    void normalCompletionStillWorksUnaffectedByTheFix() throws Exception {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/mock/stream", exchange -> {
            exchange.getRequestBody().readAllBytes();
            exchange.getResponseHeaders().set("Content-Type", "text/event-stream");
            exchange.sendResponseHeaders(200, 0);
            try (OutputStream os = exchange.getResponseBody()) {
                os.write("event: delta\ndata: {\"sequence\":1}\n\n".getBytes(StandardCharsets.UTF_8));
                os.write("event: final\ndata: {\"status\":\"COMPLETED\"}\n\n".getBytes(StandardCharsets.UTF_8));
                os.flush();
            }
        });
        server.start();
        int port = server.getAddress().getPort();

        MockLlmClient client = new MockLlmClient("http://127.0.0.1:" + port, 5000, 5000);
        GatewayMetrics metrics = new GatewayMetrics(new SimpleMeterRegistry());
        BlockingMockLlmRelay relay = new BlockingMockLlmRelay(client, metrics);
        RequestLifecycle lifecycle = new RequestLifecycle("normal-completion", 60000);
        ThreadPoolExecutor writeExecutor = new ThreadPoolExecutor(1, 1, 0L, TimeUnit.SECONDS, new ArrayBlockingQueue<>(100));
        AtomicReference<Outcome> finalOutcome = new AtomicReference<>();
        CountDownLatch terminalLatch = new CountDownLatch(1);
        PerStreamWriteChannel channel = buildChannel(lifecycle, writeExecutor, metrics, finalOutcome, terminalLatch);

        relay.relay(lifecycle, "{}", channel);

        assertTrue(terminalLatch.await(5, TimeUnit.SECONDS));
        assertEquals(Outcome.COMPLETED, finalOutcome.get());
        assertNotEquals(Outcome.TIMEOUT, finalOutcome.get());

        writeExecutor.shutdown();
        assertTrue(writeExecutor.awaitTermination(5, TimeUnit.SECONDS));
    }
}
