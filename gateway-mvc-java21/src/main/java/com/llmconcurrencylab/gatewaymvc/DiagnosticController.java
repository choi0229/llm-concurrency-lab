package com.llmconcurrencylab.gatewaymvc;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

import com.llmconcurrencylab.gatewaymvc.executor.ChatTaskSubmitter;
import com.llmconcurrencylab.gatewaymvc.executor.VirtualLimitedTaskSubmitter;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Diagnostic-only endpoints for Unit 3 smoke testing (docs/test-plan/
 * phase2-design.md Unit 3 sections 5 and 9) — NOT Prometheus metrics, not
 * used by any formal benchmark. Deliberately exposes plain values (thread
 * name, permit count) rather than adding these as Prometheus labels — Unit 3
 * section 3/5 explicitly says not to put thread name/id into Prometheus
 * labels (unbounded cardinality risk).
 */
@RestController
public class DiagnosticController {

    private final ChatTaskSubmitter taskSubmitter;

    @Autowired
    public DiagnosticController(ChatTaskSubmitter taskSubmitter) {
        this.taskSubmitter = taskSubmitter;
    }

    /**
     * Confirms which thread services THIS Tomcat request — used once to
     * verify Tomcat's own request-handling threads stay platform threads
     * regardless of THREAD_MODE (Unit 3 section 5: Spring Boot's global
     * spring.threads.virtual.enabled is never set in this module).
     */
    @GetMapping("/debug/thread")
    public Map<String, Object> threadInfo() {
        Thread t = Thread.currentThread();
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("threadName", t.getName());
        result.put("isVirtual", t.isVirtual());
        // Scheduler metadata (Unit 3.5 section 6) — recorded, never tuned.
        result.put("availableProcessors", Runtime.getRuntime().availableProcessors());
        return result;
    }

    /** VT-Limited only — n/a under other THREAD_MODE values. */
    @GetMapping("/debug/permits")
    public Map<String, Object> permits() {
        if (taskSubmitter instanceof VirtualLimitedTaskSubmitter) {
            int available = ((VirtualLimitedTaskSubmitter) taskSubmitter).availablePermits();
            return Collections.singletonMap("availablePermits", available);
        }
        return Collections.singletonMap("availablePermits", "n/a (THREAD_MODE != VIRTUAL_LIMITED)");
    }
}
