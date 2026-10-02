package com.finsight.common.mq;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import lombok.RequiredArgsConstructor;
import org.springframework.amqp.core.ReturnedMessage;
import org.springframework.amqp.rabbit.connection.CorrelationData;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.time.LocalDateTime;

@Component
@RequiredArgsConstructor
public class MqOutboxConfirmHandler {

    private static final Duration RETRY_DELAY = Duration.ofSeconds(30);
    private static final int MAX_ERROR_LENGTH = 1000;

    private final MqOutboxMessageMapper outboxMapper;

    public void handleConfirm(CorrelationData correlation, boolean ack, String cause) {
        if (correlation == null || correlation.getId() == null || correlation.getId().isBlank()) {
            return;
        }
        if (ack) {
            markSent(correlation.getId());
        } else {
            markFailed(correlation.getId(), cause == null ? "publisher confirm nack" : cause);
        }
    }

    public void handleReturned(ReturnedMessage returned) {
        Object header = returned.getMessage().getMessageProperties().getHeaders()
                .get(MqOutboxService.HEADER_OUTBOX_ID);
        if (header == null) {
            return;
        }
        markFailed(String.valueOf(header),
                "returned: replyCode=" + returned.getReplyCode() + ", replyText=" + returned.getReplyText());
    }

    private void markSent(String messageUid) {
        MqOutboxMessage update = new MqOutboxMessage();
        update.setStatus(MqOutboxMessage.STATUS_SENT);
        update.setSentAt(LocalDateTime.now());
        update.setLastError("");
        outboxMapper.update(update, new LambdaUpdateWrapper<MqOutboxMessage>()
                .eq(MqOutboxMessage::getMessageUid, messageUid)
                .eq(MqOutboxMessage::getStatus, MqOutboxMessage.STATUS_PENDING));
    }

    private void markFailed(String messageUid, String reason) {
        MqOutboxMessage message = outboxMapper.selectOne(new LambdaQueryWrapper<MqOutboxMessage>()
                .eq(MqOutboxMessage::getMessageUid, messageUid)
                .last("LIMIT 1"));
        if (message == null || MqOutboxMessage.STATUS_SENT.equals(message.getStatus())) {
            return;
        }
        int retryCount = message.getRetryCount() == null ? 0 : message.getRetryCount();
        message.setStatus(MqOutboxMessage.STATUS_FAILED);
        message.setRetryCount(retryCount + 1);
        message.setLastError(truncate(reason));
        message.setNextRetryAt(LocalDateTime.now().plus(RETRY_DELAY));
        outboxMapper.updateById(message);
    }

    private static String truncate(String value) {
        String text = String.valueOf(value == null ? "" : value);
        return text.length() <= MAX_ERROR_LENGTH ? text : text.substring(0, MAX_ERROR_LENGTH);
    }
}
