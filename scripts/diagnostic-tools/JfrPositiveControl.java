/**
 * Phase 2 Unit 8 B-0 — JFR pipeline positive control.
 *
 * Standalone diagnostic program, deliberately NOT part of gateway-mvc-java21
 * production code (docs/test-results/phase2/secondary/jfr-pinning-diagnostic/).
 * Its only job is to prove that a jdk.VirtualThreadPinned event CAN be
 * captured with the JFC settings/threshold this Unit uses for B-1/B-2 —
 * before trusting a "0 events" result from the real Gateway workload.
 *
 * Known JDK 21 pinning trigger: a virtual thread blocking inside a
 * `synchronized` block cannot unmount from its carrier (this is exactly the
 * pinning gap JEP 491 removed in JDK 24 — see docs/test-plan/
 * phase2-design.md section 6). Each of 5 virtual threads here enters a
 * synchronized block and Thread.sleep()s ~300ms while holding it.
 */
public class JfrPositiveControl {
    private static final Object LOCK = new Object();
    private static final int TASK_COUNT = 5;
    private static final long SLEEP_MS = 300;

    public static void main(String[] args) throws InterruptedException {
        System.out.println("JfrPositiveControl: starting " + TASK_COUNT
                + " virtual threads, each pinning inside synchronized for ~" + SLEEP_MS + "ms");

        Thread[] threads = new Thread[TASK_COUNT];
        for (int i = 0; i < TASK_COUNT; i++) {
            threads[i] = Thread.ofVirtual().start(() -> {
                synchronized (LOCK) {
                    try {
                        Thread.sleep(SLEEP_MS);
                    } catch (InterruptedException e) {
                        Thread.currentThread().interrupt();
                    }
                }
            });
        }
        for (Thread t : threads) {
            t.join();
        }
        System.out.println("JfrPositiveControl: all " + TASK_COUNT + " virtual threads completed");
    }
}
