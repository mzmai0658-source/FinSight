package com.finsight.common.mq;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.core.MessagePostProcessor;
import org.springframework.amqp.rabbit.connection.CorrelationData;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

@Slf4j
@Service
@RequiredArgsConstructor
public class MqOutboxService {

    public static final String HEADER_OUTBOX_ID = "x-outbox-id";

    private static final int MAX_ERROR_LENGTH = 1000;
    private static final Duration BASE_RETRY_DELAY = Duration.ofSeconds(30);
    private static final Duration MAX_RETRY_DELAY = Duration.ofMinutes(5);

    private final MqOutboxMessageMapper outboxMapper;
    private final RabbitTemplate rabbitTemplate;
    private final ObjectMapper objectMapper;

    public void publish(String exchange, String routingKey, Object payload) {
        MqOutboxMessage message = new MqOutboxMessage();
        message.setMessageUid(UUID.randomUUID().toString());
        message.setExchangeName(exchange);
        message.setRoutingKey(routingKey);
        message.setPayloadType(payload.getClass().getName());
        message.setPayloadJson(writePayload(payload));
        message.setStatus(MqOutboxMessage.STATUS_PENDING);
        message.setRetryCount(0);
        message.setLastError("");
        message.setNextRetryAt(LocalDateTime.now().plus(BASE_RETRY_DELAY));
        if (outboxMapper.insert(message) != 1) {
            throw new IllegalStateException("Outbox message was not persisted");
        }

        // 作品说明：任务事务提交后，调度器才能读取对应发件箱消息。
        // 作品说明：避免在任务未提交时发送消息，使快速消费者确认尚不可见的任务。
        if (TransactionSynchronizationManager.isActualTransactionActive()) {
            return;
        }

        try {
            send(message, payload);
        } catch (Exception e) {
            markFailed(message, e.toString());
            log.warn("[mq-outbox] initial publish failed uid={} rk={}: {}",
                    message.getMessageUid(), routingKey, e.toString());
        }
    }

    public int retryDueMessages(int limit, int maxAttempts) {
        LocalDateTime now = LocalDateTime.now();
        List<MqOutboxMessage> dueMessages = outboxMapper.selectList(
                new LambdaQueryWrapper<MqOutboxMessage>()
                        .in(MqOutboxMessage::getStatus, MqOutboxMessage.STATUS_PENDING, MqOutboxMessage.STATUS_FAILED)
                        .le(MqOutboxMessage::getNextRetryAt, now)
                        .lt(MqOutboxMessage::getRetryCount, maxAttempts)
                        .orderByAsc(MqOutboxMessage::getId)
                        .last("LIMIT " + Math.max(1, limit)));
        int sent = 0;
        for (MqOutboxMessage message : dueMessages) {
            try {
                Object payload = readPayload(message);
                message.setStatus(MqOutboxMessage.STATUS_PENDING);
                message.setLastError("");
                message.setNextRetryAt(LocalDateTime.now().plus(BASE_RETRY_DELAY));
                outboxMapper.updateById(message);
                send(message, payload);
                sent++;
            } catch (Exception e) {
                markFailed(message, e.toString());
                log.warn("[mq-outbox] retry publish failed uid={} rk={}: {}",
                        message.getMessageUid(), message.getRoutingKey(), e.toString());
            }
        }
        return sent;
    }

    private void send(MqOutboxMessage message, Object payload) {
        MessagePostProcessor postProcessor = amqpMessage -> {
            amqpMessage.getMessageProperties().setMessageId(message.getMessageUid());
            amqpMessage.getMessageProperties().setHeader(HEADER_OUTBOX_ID, message.getMessageUid());
            return amqpMessage;
        };
        rabbitTemplate.convertAndSend(
                message.getExchangeName(),
                message.getRoutingKey(),
                payload,
                postProcessor,
                new CorrelationData(message.getMessageUid()));
    }

    private void markFailed(MqOutboxMessage message, String reason) {
        int nextRetryCount = safeRetryCount(message) + 1;
        message.setStatus(MqOutboxMessage.STATUS_FAILED);
        message.setRetryCount(nextRetryCount);
        message.setLastError(truncate(reason));
        message.setNextRetryAt(LocalDateTime.now().plus(backoff(nextRetryCount)));
        outboxMapper.updateById(message);
    }

    private Object readPayload(MqOutboxMessage message) {
        try {
            Class<?> payloadClass = Class.forName(message.getPayloadType());
            return objectMapper.readValue(message.getPayloadJson(), payloadClass);
        } catch (Exception e) {
            throw new IllegalStateException("restore payload failed: " + e.getMessage(), e);
        }
    }

    private String writePayload(Object payload) {
        try {
            return objectMapper.writeValueAsString(payload);
        } catch (Exception e) {
            throw new IllegalStateException("serialize payload failed: " + e.getMessage(), e);
        }
    }

    private static int safeRetryCount(MqOutboxMessage message) {
        return message.getRetryCount() == null ? 0 : message.getRetryCount();
    }

    private static Duration backoff(int retryCount) {
        long seconds = BASE_RETRY_DELAY.toSeconds() * (1L << Math.min(Math.max(retryCount - 1, 0), 4));
        return Duration.ofSeconds(Math.min(seconds, MAX_RETRY_DELAY.toSeconds()));
    }

    private static String truncate(String value) {
        String text = String.valueOf(value == null ? "" : value);
        return text.length() <= MAX_ERROR_LENGTH ? text : text.substring(0, MAX_ERROR_LENGTH);
    }
}
