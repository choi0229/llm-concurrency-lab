package com.llmconcurrencylab.gatewaymvc.sse;

import org.junit.jupiter.api.Test;
import org.springframework.http.codec.ServerSentEvent;

import static org.assertj.core.api.Assertions.assertThat;

class SseFrameFormatterTest {

    @Test
    void reconstructsMockLlmDeltaFrameExactly() {
        ServerSentEvent<String> sse = ServerSentEvent.<String>builder()
                .event("delta")
                .data("{\"sequence\":1,\"text\":\"xxxxxxxxxxxxxxxx\"}")
                .build();

        assertThat(SseFrameFormatter.format(sse))
                .isEqualTo("event: delta\ndata: {\"sequence\":1,\"text\":\"xxxxxxxxxxxxxxxx\"}\n\n");
    }

    @Test
    void reconstructsMockLlmFinalFrameExactly() {
        ServerSentEvent<String> sse = ServerSentEvent.<String>builder()
                .event("final")
                .data("{\"status\":\"COMPLETED\"}")
                .build();

        assertThat(SseFrameFormatter.format(sse)).isEqualTo("event: final\ndata: {\"status\":\"COMPLETED\"}\n\n");
    }

    @Test
    void reconstructsMockLlmErrorFrameExactly() {
        ServerSentEvent<String> sse = ServerSentEvent.<String>builder()
                .event("error")
                .data("{\"status\":\"FAILED\",\"reason\":\"mid_stream_failure\"}")
                .build();

        assertThat(SseFrameFormatter.format(sse))
                .isEqualTo("event: error\ndata: {\"status\":\"FAILED\",\"reason\":\"mid_stream_failure\"}\n\n");
    }
}
