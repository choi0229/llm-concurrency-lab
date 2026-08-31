package com.llmconcurrencylab.phase4.webflux;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.reactive.function.server.RouterFunction;
import org.springframework.web.reactive.function.server.RouterFunctions;
import org.springframework.web.reactive.function.server.ServerResponse;

import static org.springframework.web.reactive.function.server.RequestPredicates.POST;
import static org.springframework.web.reactive.function.server.RequestPredicates.GET;

@Configuration
public class ChatRouter {

    @Bean
    public RouterFunction<ServerResponse> routes(ChatHandler chatHandler) {
        return RouterFunctions.route(POST("/chat/stream"), chatHandler::stream)
                .andRoute(GET("/healthz"), req -> ServerResponse.ok().bodyValue(java.util.Map.of("status", "ok")));
    }
}
