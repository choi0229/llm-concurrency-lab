package spike;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.http.client.reactive.ReactorClientHttpConnector;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;
import reactor.netty.http.client.HttpClient;

import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

@SpringBootApplication
public class SpikeWebfluxTopologyApplication {
    public static void main(String[] args) {
        SpringApplication.run(SpikeWebfluxTopologyApplication.class, args);
    }

    @RestController
    public static class ProbeController {
        static final Set<String> serverThreads = ConcurrentHashMap.newKeySet();
        static final Set<String> clientThreads = ConcurrentHashMap.newKeySet();

        // Manually-built WebClient, exactly like WebClientConfig in gateway-mvc-webclient (P3-B):
        // HttpClient.create() with NO explicit .runOn(LoopResources) call.
        private final WebClient webClient = WebClient.builder()
                .clientConnector(new ReactorClientHttpConnector(HttpClient.create()))
                .build();

        @GetMapping("/server-echo")
        public String echo() {
            serverThreads.add(Thread.currentThread().getName());
            return "ok";
        }

        @GetMapping("/probe")
        public Mono<String> probe() {
            return webClient.get().uri("http://localhost:18095/server-echo").retrieve().bodyToMono(String.class)
                    .doOnNext(s -> clientThreads.add(Thread.currentThread().getName()));
        }

        @GetMapping("/report")
        public String report() {
            java.util.List<String> all = new java.util.ArrayList<>();
            for (Thread t : Thread.getAllStackTraces().keySet()) {
                if (t.getName().contains("nio") || t.getName().contains("reactor")) {
                    all.add(t.getName());
                }
            }
            java.util.Collections.sort(all);
            StringBuilder sb = new StringBuilder();
            sb.append("serverThreads=").append(serverThreads).append("\n");
            sb.append("clientThreads=").append(clientThreads).append("\n");
            Set<String> intersection = new java.util.HashSet<>(serverThreads);
            intersection.retainAll(clientThreads);
            sb.append("intersection=").append(intersection).append("\n");
            sb.append("allNioReactorThreadsInJvm=").append(all).append("\n");
            sb.append("availableProcessors=").append(Runtime.getRuntime().availableProcessors()).append("\n");
            return sb.toString();
        }
    }
}
