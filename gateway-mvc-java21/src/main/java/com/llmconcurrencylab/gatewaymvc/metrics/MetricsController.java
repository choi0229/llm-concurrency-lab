package com.llmconcurrencylab.gatewaymvc.metrics;

import java.io.StringWriter;

import io.prometheus.client.CollectorRegistry;
import io.prometheus.client.exporter.common.TextFormat;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class MetricsController {

    @GetMapping(value = "/metrics", produces = TextFormat.CONTENT_TYPE_004)
    public String metrics() throws java.io.IOException {
        StringWriter writer = new StringWriter();
        TextFormat.write004(writer, CollectorRegistry.defaultRegistry.metricFamilySamples());
        return writer.toString();
    }
}
