package com.llmconcurrencylab.gatewaymvc.metrics;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.ThreadPoolExecutor;

import io.prometheus.client.Collector;

/**
 * Reads the executor's own getters at scrape time rather than deriving state
 * from other counters. In particular gateway_executor_queue_size comes
 * straight from executor.getQueue().size() — the source of truth — not from
 * (async_active - active_threads), which is only an approximation.
 */
public class ExecutorStateCollector extends Collector {

    private final ThreadPoolExecutor executor;

    public ExecutorStateCollector(ThreadPoolExecutor executor) {
        this.executor = executor;
    }

    @Override
    public List<MetricFamilySamples> collect() {
        List<MetricFamilySamples> families = new ArrayList<MetricFamilySamples>();
        families.add(gauge("gateway_executor_pool_size", "Current number of threads in the pool", executor.getPoolSize()));
        families.add(gauge("gateway_executor_active_threads", "Threads currently executing a task", executor.getActiveCount()));
        families.add(gauge("gateway_executor_largest_pool_size", "Largest pool size ever reached", executor.getLargestPoolSize()));
        families.add(gauge("gateway_executor_queue_size", "Tasks currently sitting in the executor queue (source of truth)", executor.getQueue().size()));
        families.add(gauge("gateway_executor_completed_task_total", "Tasks completed by the executor", executor.getCompletedTaskCount()));
        return families;
    }

    private MetricFamilySamples gauge(String name, String help, double value) {
        MetricFamilySamples.Sample sample = new MetricFamilySamples.Sample(
                name, Collections.<String>emptyList(), Collections.<String>emptyList(), value);
        return new MetricFamilySamples(name, Type.GAUGE, help, Collections.singletonList(sample));
    }
}
