package com.finsight.common.mq;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Slf4j
@Component
@RequiredArgsConstructor
public class MqOutboxRetryScheduler {

    private final MqOutboxService outboxService;

    @Value("${finsight.mq.outbox.batch-size:50}")
    private int batchSize;

    @Value("${finsight.mq.outbox.max-attempts:8}")
    private int maxAttempts;

    @Scheduled(fixedDelayString = "${finsight.mq.outbox.retry-delay:30s}")
    public void retryDueMessages() {
        int retried = outboxService.retryDueMessages(batchSize, maxAttempts);
        if (retried > 0) {
            log.info("[mq-outbox] retried {} due messages", retried);
        }
    }
}
