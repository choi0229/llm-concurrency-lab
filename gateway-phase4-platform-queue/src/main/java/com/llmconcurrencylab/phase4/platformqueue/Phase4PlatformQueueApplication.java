package com.llmconcurrencylab.phase4.platformqueue;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication(scanBasePackages = {
        "com.llmconcurrencylab.phase4.platformqueue",
        "com.llmconcurrencylab.phase4.common"
})
public class Phase4PlatformQueueApplication {

    public static void main(String[] args) {
        SpringApplication.run(Phase4PlatformQueueApplication.class, args);
    }
}
