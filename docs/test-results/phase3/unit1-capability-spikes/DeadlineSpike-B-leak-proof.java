import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;
import java.time.Duration;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.concurrent.atomic.AtomicInteger;

public class DeadlineSpike3 {
    public static void main(String[] args) throws Exception {
        AtomicInteger rawTicks = new AtomicInteger(0);
        Flux<Integer> data = Flux.interval(Duration.ofMillis(100))
                .doOnNext(t -> rawTicks.incrementAndGet())
                .map(Long::intValue);
        Mono<Integer> deadlineSignal = Mono.<Integer>delay(Duration.ofMillis(300))
                .flatMap(tick -> Mono.error(new TimeoutException("absolute-deadline-exceeded")));
        Flux<Integer> merged = data.takeUntilOther(deadlineSignal);

        CountDownLatch latch = new CountDownLatch(1);
        merged.subscribe(v -> {}, e -> latch.countDown(), latch::countDown);
        latch.await(2, TimeUnit.SECONDS);
        System.out.println("rawTicks at termination (~300ms): " + rawTicks.get());
        Thread.sleep(2000);
        System.out.println("rawTicks after +2000ms more: " + rawTicks.get() + " (if still growing => upstream interval NOT cancelled => leak)");
        System.exit(0);
    }
}
