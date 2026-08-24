import reactor.netty.http.client.HttpClient;
import reactor.netty.resources.ConnectionProvider;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;

import java.time.Duration;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicReference;

public class PoolSpike {
    public static void main(String[] args) throws Exception {
        int port = Integer.parseInt(args[0]);
        // maxConnections=1, pendingAcquireMaxCount=0, pendingAcquireTimeout=300ms
        ConnectionProvider provider = ConnectionProvider.builder("spike-pool")
                .maxConnections(1)
                .pendingAcquireMaxCount(1)
                .pendingAcquireTimeout(Duration.ofMillis(300))
                .build();

        HttpClient client = HttpClient.create(provider).baseUrl("http://127.0.0.1:" + port);

        final AtomicReference<String> result1 = new AtomicReference<>("PENDING");
        final AtomicReference<String> result2 = new AtomicReference<>("PENDING");
        final AtomicReference<Long> elapsed2 = new AtomicReference<>(-1L);
        CountDownLatch latch = new CountDownLatch(2);

        long t0 = System.nanoTime();

        // request 1: occupies the sole connection for 3s (server-side sleep)
        Mono<String> req1 = client.get().uri("/one").responseContent().aggregate().asString()
                .doOnNext(s -> result1.set("OK:" + s))
                .doOnError(e -> result1.set("ERROR:" + e.getClass().getName() + ":" + e.getMessage()))
                .doFinally(sig -> latch.countDown());

        // request 2 fired ~100ms later, while req1 still holds the only connection
        Mono<String> req2 = Mono.delay(Duration.ofMillis(100))
                .then(Mono.fromCallable(System::nanoTime))
                .flatMap(startNanos -> client.get().uri("/two").responseContent().aggregate().asString()
                        .doOnNext(s -> result2.set("OK:" + s))
                        .doOnError(e -> {
                            elapsed2.set((System.nanoTime() - startNanos) / 1_000_000);
                            result2.set("ERROR:" + e.getClass().getName() + ":" + e.getMessage());
                        })
                )
                .doFinally(sig -> latch.countDown());

        req1.subscribeOn(Schedulers.boundedElastic()).subscribe();
        req2.subscribeOn(Schedulers.boundedElastic()).subscribe();

        latch.await();
        long totalMs = (System.nanoTime() - t0) / 1_000_000;

        System.out.println("=== PoolSpike result (maxConnections=1, pendingAcquireMaxCount=0, pendingAcquireTimeout=300ms) ===");
        System.out.println("request1 (holds sole connection 3s): " + result1.get());
        System.out.println("request2 (fired ~100ms later while pool full): " + result2.get());
        System.out.println("request2 time-to-error (ms, measured from its own start, excludes the 100ms delay): " + elapsed2.get());
        System.out.println("total wall time (ms): " + totalMs);
        provider.dispose();
        System.exit(0);
    }
}
