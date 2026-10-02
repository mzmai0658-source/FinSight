package com.finsight.common.mq;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.finsight.etl.mq.EtlTaskMessage;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.amqp.AmqpException;
import org.springframework.amqp.core.MessagePostProcessor;
import org.springframework.amqp.rabbit.connection.CorrelationData;
import org.springframework.amqp.rabbit.core.RabbitTemplate;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;
import org.springframework.transaction.support.TransactionSynchronizationManager;

@ExtendWith(MockitoExtension.class)
class MqOutboxServiceTest {

    @Mock
    private MqOutboxMessageMapper outboxMapper;
    @Mock
    private RabbitTemplate rabbitTemplate;

    private MqOutboxService service;

    @BeforeEach
    void setUp() {
        service = new MqOutboxService(outboxMapper, rabbitTemplate, new ObjectMapper());
    }

    @Test
    void publishPersistsPendingMessageBeforeSendingWithCorrelationId() {
        ArgumentCaptor<MqOutboxMessage> inserted = ArgumentCaptor.forClass(MqOutboxMessage.class);
        doAnswer(invocation -> {
            MqOutboxMessage message = invocation.getArgument(0);
            message.setId(11L);
            return 1;
        }).when(outboxMapper).insert(inserted.capture());

        service.publish("finsight.topic", "etl.task", new EtlTaskMessage(42L));

        MqOutboxMessage saved = inserted.getValue();
        assertThat(saved.getStatus()).isEqualTo(MqOutboxMessage.STATUS_PENDING);
        assertThat(saved.getMessageUid()).isNotBlank();
        assertThat(saved.getPayloadJson()).contains("\"taskId\":42");
        assertThat(saved.getPayloadType()).isEqualTo(EtlTaskMessage.class.getName());
        verify(rabbitTemplate).convertAndSend(
                eq("finsight.topic"),
                eq("etl.task"),
                eq(new EtlTaskMessage(42L)),
                any(MessagePostProcessor.class),
                any(CorrelationData.class));
    }

    @Test
    void publishFailureKeepsOutboxMessageForScheduledRetry() {
        ArgumentCaptor<MqOutboxMessage> inserted = ArgumentCaptor.forClass(MqOutboxMessage.class);
        doAnswer(invocation -> {
            MqOutboxMessage message = invocation.getArgument(0);
            message.setId(12L);
            return 1;
        }).when(outboxMapper).insert(inserted.capture());
        doThrow(new AmqpException("broker down")).when(rabbitTemplate).convertAndSend(
                eq("finsight.topic"),
                eq("etl.task"),
                any(Object.class),
                any(MessagePostProcessor.class),
                any(CorrelationData.class));

        service.publish("finsight.topic", "etl.task", new EtlTaskMessage(43L));

        MqOutboxMessage saved = inserted.getValue();
        assertThat(saved.getStatus()).isEqualTo(MqOutboxMessage.STATUS_FAILED);
        assertThat(saved.getRetryCount()).isEqualTo(1);
        assertThat(saved.getLastError()).contains("broker down");
        assertThat(saved.getNextRetryAt()).isNotNull();
        verify(outboxMapper).updateById(saved);
    }

    @Test
    void taskTransactionLeavesDeliveryForSchedulerAfterCommit() {
        when(outboxMapper.insert(any(MqOutboxMessage.class))).thenReturn(1);
        TransactionSynchronizationManager.setActualTransactionActive(true);
        try {
            service.publish("finsight.topic", "etl.task", new EtlTaskMessage(44L));
            verify(outboxMapper).insert(any(MqOutboxMessage.class));
            verifyNoInteractions(rabbitTemplate);
        } finally {
            TransactionSynchronizationManager.setActualTransactionActive(false);
        }
    }
}
