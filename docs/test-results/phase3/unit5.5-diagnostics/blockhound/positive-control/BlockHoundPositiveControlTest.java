package diag;

import java.util.concurrent.Callable;
import java.util.concurrent.FutureTask;
import java.util.concurrent.TimeUnit;

import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import reactor.blockhound.BlockHound;
import reactor.blockhound.BlockingOperationError;
import reactor.core.scheduler.Schedulers;

import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.fail;

/**
 * Unit 5.5 §4 — before pointing BlockHound at either P3-B or P3-C, confirm the pipeline can
 * actually detect a real blocking call on this exact runtime (Java 8 / Zulu, reactor-core
 * 3.4.34). The canonical validation pattern is BlockHound's own official one
 * (docs/quick_start.md: "it is highly recommended to add a dummy test with a well-known blocking
 * call... Thread.sleep() in a reactive chain should trigger detection").
 */
class BlockHoundPositiveControlTest {

    @BeforeAll
    static void installBlockHound() {
        BlockHound.install();
    }

    @Test
    void threadSleepOnANonBlockingSchedulerIsDetected() throws Exception {
        FutureTask<?> task = new FutureTask<>((Callable<Object>) () -> {
            Thread.sleep(0);
            return "";
        });
        Schedulers.parallel().schedule(task);
        try {
            task.get(10, TimeUnit.SECONDS);
            fail("Expected a BlockingOperationError to be thrown by the scheduled task, but it completed normally "
                    + "-- BlockHound did NOT detect the blocking call on this runtime.");
        } catch (java.util.concurrent.ExecutionException e) {
            assertInstanceOf(BlockingOperationError.class, e.getCause(),
                    "Task failed, but not with BlockingOperationError -- got: " + e.getCause());
            System.out.println("POSITIVE_CONTROL_RESULT=DETECTED: " + e.getCause());
        }
    }
}
