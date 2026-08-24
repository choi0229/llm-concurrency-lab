import reactor.core.publisher.Mono;
import reactor.netty.DisposableServer;
import reactor.netty.http.client.HttpClient;
import reactor.netty.http.server.HttpServer;

import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/**
 * Unit 4-0 audit: does a default (no explicit LoopResources) reactor-netty HttpServer and a
 * default (no explicit LoopResources) reactor-netty HttpClient, both created with zero-arg/
 * no-runOn() factory calls in the SAME JVM, end up sharing event-loop threads? Verified by actual
 * thread-name evidence, not by reading javadoc.
 */
public class ResourceTopologySpike {
    public static void main(String[] args) throws Exception {
        Set<String> serverThreads = ConcurrentHashMap.newKeySet();
        Set<String> clientThreads = ConcurrentHashMap.newKeySet();

        // Server: HttpServer.create() with no .runOn(...) call -- default resources.
        DisposableServer server = HttpServer.create()
                .host("127.0.0.1")
                .port(0)
                .handle((req, res) -> {
                    serverThreads.add(Thread.currentThread().getName());
                    return res.sendString(Mono.just("ok"));
                })
                .bindNow();

        int port = server.port();
        System.out.println("server bound on port " + port);

        // Client: HttpClient.create() with no .runOn(...) call -- default resources.
        HttpClient client = HttpClient.create();

        CountDownLatch latch = new CountDownLatch(20);
        for (int i = 0; i < 20; i++) {
            client.get()
                    .uri("http://127.0.0.1:" + port + "/")
                    .responseContent()
                    .aggregate()
                    .asString()
                    .doOnNext(s -> clientThreads.add(Thread.currentThread().getName()))
                    .doFinally(sig -> latch.countDown())
                    .subscribe();
            Thread.sleep(20); // spread requests slightly so different event-loop threads get used
        }
        latch.await(10, TimeUnit.SECONDS);

        System.out.println("=== server threads used ===");
        serverThreads.forEach(System.out::println);
        System.out.println("=== client threads used ===");
        clientThreads.forEach(System.out::println);

        Set<String> intersection = new java.util.HashSet<>(serverThreads);
        intersection.retainAll(clientThreads);
        System.out.println("=== intersection (threads used by BOTH server and client) ===");
        System.out.println(intersection);
        System.out.println("shared=" + !intersection.isEmpty());

        System.out.println("=== ALL reactor-http-nio-* threads alive in this JVM right now ===");
        java.util.List<String> allReactorThreads = new java.util.ArrayList<>();
        for (Thread t : Thread.getAllStackTraces().keySet()) {
            if (t.getName().startsWith("reactor-http-nio")) {
                allReactorThreads.add(t.getName());
            }
        }
        java.util.Collections.sort(allReactorThreads);
        allReactorThreads.forEach(System.out::println);
        System.out.println("total distinct reactor-http-nio-* threads in JVM: " + allReactorThreads.size());
        System.out.println("availableProcessors: " + Runtime.getRuntime().availableProcessors());

        server.disposeNow();
        System.exit(0);
    }
}
