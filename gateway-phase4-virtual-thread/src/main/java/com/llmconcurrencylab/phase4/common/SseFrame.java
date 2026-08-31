package com.llmconcurrencylab.phase4.common;

/**
 * One parsed SSE frame relayed from Mock LLM. `format()` is the wire representation this Gateway
 * writes to the client — SSE parser-level parity (event name / payload / order / final) is the
 * cross-model contract, not byte-identical whitespace (docs/test-plan/phase4-design.md section 8).
 */
public record SseFrame(String event, String data) {

    public String format() {
        return "event: " + event + "\ndata: " + data + "\n\n";
    }
}
