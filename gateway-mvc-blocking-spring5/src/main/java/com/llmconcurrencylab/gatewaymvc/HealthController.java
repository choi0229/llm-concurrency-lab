package com.llmconcurrencylab.gatewaymvc;

import java.util.Collections;
import java.util.Map;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/** Ported from Phase 1/2 (repo convention) — Actuator's own /actuator/health is also exposed. */
@RestController
public class HealthController {

    @GetMapping("/healthz")
    public Map<String, String> healthz() {
        return Collections.singletonMap("status", "ok");
    }
}
