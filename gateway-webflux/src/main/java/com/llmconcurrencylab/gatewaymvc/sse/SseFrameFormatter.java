package com.llmconcurrencylab.gatewaymvc.sse;

import org.springframework.http.codec.ServerSentEvent;

/**
 * Reconstructs the exact wire format P3-A relays byte-for-byte (SseFrameReader — "event:
 * &lt;name&gt;\ndata: &lt;payload&gt;\n\n") from Spring's decoded {@link ServerSentEvent}. Unit 3
 * §5's decoder choice: WebClient decodes into {@code ServerSentEvent<String>} via Spring's own
 * SSE codec (Spring's official parser, not a hand-rolled one) rather than raw incremental
 * DataBuffer parsing — safe here because Mock LLM's own {@code sse_event()} function
 * (mock-llm-fastapi/app/main.py) always emits exactly this 2-field shape (event + data, never
 * multi-line data, comments, id, or retry) for every event it sends (delta/final/error). Given
 * that fixed shape, reconstructing the frame from the decoded fields reproduces Mock LLM's
 * original bytes exactly. Unit 1's client-observable contract (docs/test-plan/phase3-design.md
 * §4) only requires identical event sequence/payload, not identical chunk boundaries, so this
 * choice satisfies the contract even in the hypothetical case where reconstruction and the
 * original bytes diverge in whitespace that doesn't affect the parsed event.
 */
public final class SseFrameFormatter {

    private SseFrameFormatter() {
    }

    public static String format(ServerSentEvent<String> event) {
        String name = event.event() != null ? event.event() : "message";
        String data = event.data() != null ? event.data() : "";
        return "event: " + name + "\ndata: " + data + "\n\n";
    }
}
