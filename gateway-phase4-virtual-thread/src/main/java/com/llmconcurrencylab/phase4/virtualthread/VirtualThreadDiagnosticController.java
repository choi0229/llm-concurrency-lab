package com.llmconcurrencylab.phase4.virtualthread;

import java.util.Map;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Functional diagnostic only (Unit 2 F9, docs/test-plan/phase4-design.md section 5) — not on the
 * /chat/stream hot path, no scalability-relevant logging. Confirms the real outbound executor
 * bean actually produces {@code Thread.isVirtual()==true} tasks, rather than relying only on the
 * disposable Unit 0 spike.
 */
@RestController
public class VirtualThreadDiagnosticController {

    private final ExecutorService virtualOutboundExecutor;

    public VirtualThreadDiagnosticController(ExecutorService virtualOutboundExecutor) {
        this.virtualOutboundExecutor = virtualOutboundExecutor;
    }

    @GetMapping("/diag/virtual-thread-check")
    public Map<String, Object> check() throws ExecutionException, InterruptedException {
        var future = virtualOutboundExecutor.submit(() -> {
            Thread t = Thread.currentThread();
            Map<String, Object> result = new java.util.HashMap<>();
            result.put("thread", t.toString());
            result.put("isVirtual", t.isVirtual());
            return result;
        });
        return future.get();
    }
}
