package com.finsight.etl;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import jakarta.annotation.PostConstruct;

import java.time.Duration;

@Slf4j
@Component
@RequiredArgsConstructor
public class EtlTaskRecoveryScheduler {

    private final EtlTaskService taskService;

    @Value("${finsight.etl.run-timeout:3h}")
    private Duration runTimeout;

    @Value("${finsight.etl.request-timeout:2h}")
    private Duration requestTimeout;

    @PostConstruct
    void validateTimeouts() {
        if (runTimeout.compareTo(requestTimeout) <= 0) {
            throw new IllegalStateException("ETL_RUN_TIMEOUT must exceed ETL_REQUEST_TIMEOUT");
        }
    }

    @Value("${finsight.etl.recovery-batch-size:50}")
    private int batchSize;

    @Scheduled(fixedDelayString = "${finsight.etl.recovery-delay:60s}")
    public void recoverStaleRunningTasks() {
        int recovered = taskService.recoverStaleRunningTasks(runTimeout, batchSize);
        if (recovered > 0) {
            log.warn("[etl] recovered {} stale RUNNING tasks", recovered);
        }
    }
}
