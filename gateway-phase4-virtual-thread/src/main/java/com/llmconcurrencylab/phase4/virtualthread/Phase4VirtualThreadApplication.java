package com.llmconcurrencylab.phase4.virtualthread;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication(scanBasePackages = {
        "com.llmconcurrencylab.phase4.virtualthread",
        "com.llmconcurrencylab.phase4.common"
})
public class Phase4VirtualThreadApplication {

    public static void main(String[] args) {
        SpringApplication.run(Phase4VirtualThreadApplication.class, args);
    }
}
