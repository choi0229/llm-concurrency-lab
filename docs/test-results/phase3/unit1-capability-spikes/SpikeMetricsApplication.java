package spike;

import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.netty.http.client.HttpClient;
import reactor.netty.resources.ConnectionProvider;
import reactor.core.publisher.Mono;

import java.time.Duration;

@SpringBootApplication
public class SpikeMetricsApplication {
    public static void main(String[] args) {
        SpringApplication.run(SpikeMetricsApplication.class, args);
    }

    @RestController
    public static class SpikeController {
        private final MeterRegistry registry;
        private final WebClient webClient;
        private final io.micrometer.core.instrument.Counter requestsStarted;

        @Autowired
        public SpikeController(MeterRegistry registry) {
            this.registry = registry;
            this.requestsStarted = registry.counter("gateway.requests.started");
            ConnectionProvider provider = ConnectionProvider.builder("spike-webclient-pool")
                    .maxConnections(10)
                    .pendingAcquireTimeout(Duration.ofSeconds(5))
                    .metrics(true)
                    .build();
            HttpClient httpClient = HttpClient.create(provider).metrics(true, s -> s);
            this.webClient = WebClient.builder()
                    .clientConnector(new org.springframework.http.client.reactive.ReactorClientHttpConnector(httpClient))
                    .build();
        }

        @GetMapping("/ping")
        public String ping() {
            requestsStarted.increment();
            return "spike-metrics-ok";
        }

        @GetMapping("/call-upstream")
        public Mono<String> callUpstream() {
            return registry.timer("gateway.stream.duration").record(() ->
                    webClient.get().uri("http://127.0.0.1:18099/one")
                            .retrieve().bodyToMono(String.class)
                            .doOnNext(s -> {})
                            .onErrorReturn("error")
            );
        }
    }
}
