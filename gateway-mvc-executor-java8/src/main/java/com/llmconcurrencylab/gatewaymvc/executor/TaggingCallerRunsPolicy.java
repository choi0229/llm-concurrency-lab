package com.llmconcurrencylab.gatewaymvc.executor;

import java.util.concurrent.RejectedExecutionHandler;
import java.util.concurrent.ThreadPoolExecutor;

/**
 * Behaves exactly like ThreadPoolExecutor.CallerRunsPolicy (runs the task
 * synchronously on the thread that called execute(), typically a Tomcat
 * request thread) but first tags the task as mode=caller so
 * InstrumentedRunnable records it correctly instead of misreporting it as
 * queue wait.
 */
public class TaggingCallerRunsPolicy implements RejectedExecutionHandler {

    @Override
    public void rejectedExecution(Runnable task, ThreadPoolExecutor executor) {
        if (!executor.isShutdown()) {
            if (task instanceof InstrumentedRunnable) {
                ((InstrumentedRunnable) task).markCallerRun();
            }
            task.run();
        }
    }
}
