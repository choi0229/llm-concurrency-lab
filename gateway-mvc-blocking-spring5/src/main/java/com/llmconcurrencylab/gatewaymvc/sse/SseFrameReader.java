package com.llmconcurrencylab.gatewaymvc.sse;

import java.io.BufferedReader;
import java.io.IOException;

/**
 * Assembles raw SSE lines from Mock LLM into whole frames (Unit 2 §4 — "가능하면 line 하나씩
 * independent write task로 보내지 않는다... event/data line → blank line까지 하나의 SSE frame으로
 * 조립한다").
 *
 * A frame is the exact byte-preserving concatenation of every line from just after the previous
 * blank-line boundary up to and including the next blank line, each followed by "\n" — matching
 * Phase 1/2's per-line relay write pattern (ChatController.relay(): writer.write(line);
 * writer.write("\n"); flush() at the blank line) but emitted as one atomic write instead of N.
 * Deliberately generic about line count per frame (not hardcoded to exactly 2 content lines) so a
 * future multi-line `data:` payload from Mock LLM would not silently break framing — see Unit 2
 * §4's explicit warning against assuming the current 2-line shape.
 */
public final class SseFrameReader {

    private final BufferedReader reader;

    public SseFrameReader(BufferedReader reader) {
        this.reader = reader;
    }

    /**
     * @return the next whole frame (ending in the blank-line boundary), or {@code null} at a
     *     clean EOF with no partial data pending (Mock LLM closed the stream normally between
     *     frames). If the upstream closes mid-frame (no trailing blank line), returns whatever
     *     partial content was read — the caller treats this the same as any other frame; Mock
     *     LLM's own contract (mock-llm-fastapi/app/main.py) always terminates cleanly on a blank
     *     line, so this path is a defensive fallback rather than a normal case.
     */
    public String readFrame() throws IOException {
        StringBuilder sb = new StringBuilder();
        String line;
        while ((line = reader.readLine()) != null) {
            sb.append(line).append('\n');
            if (line.isEmpty()) {
                return sb.toString();
            }
        }
        return sb.length() > 0 ? sb.toString() : null;
    }
}
