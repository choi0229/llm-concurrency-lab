package com.llmconcurrencylab.gatewaymvc.sse;

import java.io.BufferedReader;
import java.io.StringReader;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class SseFrameReaderTest {

    /** Mock LLM's exact wire format (mock-llm-fastapi/app/main.py sse_event()): "event: X\ndata: Y\n\n". */
    @Test
    void readsDeltaThenFinalFramesInOrder() throws Exception {
        String raw = "event: delta\ndata: {\"sequence\":1,\"text\":\"xx\"}\n\n"
                + "event: delta\ndata: {\"sequence\":2,\"text\":\"xx\"}\n\n"
                + "event: final\ndata: {\"status\":\"COMPLETED\"}\n\n";
        SseFrameReader reader = new SseFrameReader(new BufferedReader(new StringReader(raw)));

        assertThat(reader.readFrame()).isEqualTo("event: delta\ndata: {\"sequence\":1,\"text\":\"xx\"}\n\n");
        assertThat(reader.readFrame()).isEqualTo("event: delta\ndata: {\"sequence\":2,\"text\":\"xx\"}\n\n");
        assertThat(reader.readFrame()).isEqualTo("event: final\ndata: {\"status\":\"COMPLETED\"}\n\n");
        assertThat(reader.readFrame()).isNull();
    }

    @Test
    void errorFrameEndsTheStream() throws Exception {
        String raw = "event: delta\ndata: {\"sequence\":1,\"text\":\"x\"}\n\n"
                + "event: error\ndata: {\"status\":\"FAILED\",\"reason\":\"mid_stream_failure\"}\n\n";
        SseFrameReader reader = new SseFrameReader(new BufferedReader(new StringReader(raw)));

        assertThat(reader.readFrame()).isEqualTo("event: delta\ndata: {\"sequence\":1,\"text\":\"x\"}\n\n");
        assertThat(reader.readFrame())
                .isEqualTo("event: error\ndata: {\"status\":\"FAILED\",\"reason\":\"mid_stream_failure\"}\n\n");
        assertThat(reader.readFrame()).isNull();
    }

    @Test
    void cleanEofWithNoPendingDataReturnsNull() throws Exception {
        SseFrameReader reader = new SseFrameReader(new BufferedReader(new StringReader("")));
        assertThat(reader.readFrame()).isNull();
    }

    @Test
    void blankLineIsWhatDelimitsAFrameNotLineCount() throws Exception {
        // Defensive case (Unit 2 §4): a hypothetical multi-line data payload must still be treated
        // as one frame as long as the blank-line boundary is respected.
        String raw = "event: delta\ndata: line1\ndata: line2\n\n";
        SseFrameReader reader = new SseFrameReader(new BufferedReader(new StringReader(raw)));
        assertThat(reader.readFrame()).isEqualTo("event: delta\ndata: line1\ndata: line2\n\n");
        assertThat(reader.readFrame()).isNull();
    }

    @Test
    void upstreamClosedMidFrameReturnsPartialContentAsBestEffort() throws Exception {
        String raw = "event: delta\ndata: {\"sequence\":1"; // no trailing blank line, upstream just closes
        SseFrameReader reader = new SseFrameReader(new BufferedReader(new StringReader(raw)));
        assertThat(reader.readFrame()).isEqualTo("event: delta\ndata: {\"sequence\":1\n");
        assertThat(reader.readFrame()).isNull();
    }
}
