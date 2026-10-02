package com.finsight.etl.mq;

import com.finsight.chat.AgentClient;
import com.finsight.common.metrics.ObservabilityService;
import com.finsight.etl.EtlTaskService;
import com.finsight.etl.EtlExecutionUncertainException;
import com.finsight.etl.entity.EtlTask;
import com.rabbitmq.client.Channel;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageProperties;
import org.springframework.data.redis.core.StringRedisTemplate;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.inOrder;

@ExtendWith(MockitoExtension.class)
class EtlTaskConsumerTest {

    @Mock
    private EtlTaskService taskService;
    @Mock
    private AgentClient agentClient;
    @Mock
    private StringRedisTemplate redis;
    @Mock
    private ObservabilityService observabilityService;
    @Mock
    private Channel channel;

    private Message amqpMessage;
    private EtlTaskConsumer consumer;

    @BeforeEach
    void setUp() {
        MessageProperties properties = new MessageProperties();
        properties.setDeliveryTag(123L);
        amqpMessage = new Message(new byte[0], properties);
        consumer = new EtlTaskConsumer(taskService, agentClient, redis, observabilityService);
    }

    @Test
    void duplicateSuccessfulTaskIsAckedWithoutCallingAgentAgain() throws Exception {
        EtlTask task = task(7L, EtlTaskService.STATUS_SUCCESS);
        when(taskService.getById(7L)).thenReturn(task);

        consumer.onTask(new EtlTaskMessage(7L), channel, amqpMessage);

        verify(channel).basicAck(123L, false);
        verifyNoInteractions(agentClient);
    }

    @Test
    void businessFailedPipelineResultIsPersistedAndAcked() throws Exception {
        EtlTask task = task(8L, EtlTaskService.STATUS_PENDING);
        Map<String, Object> report = Map.of("status", "failed", "message", "bad pdf");
        when(taskService.getById(8L)).thenReturn(task);
        when(taskService.markRunning(task)).thenReturn(true);
        when(agentClient.runEtl(task.getFilePath(), task.getFileType(), false)).thenReturn(report);

        consumer.onTask(new EtlTaskMessage(8L), channel, amqpMessage);

        verify(taskService).markRunning(task);
        verify(taskService).complete(task, report);
        verify(channel).basicAck(123L, false);
        var order = inOrder(taskService, channel);
        order.verify(taskService).complete(task, report);
        order.verify(channel).basicAck(123L, false);
    }

    @Test
    void infrastructureFailureIsMarkedFailedAndRethrownForRetryAndDlq() throws Exception {
        EtlTask task = task(9L, EtlTaskService.STATUS_PENDING);
        when(taskService.getById(9L)).thenReturn(task);
        when(taskService.markRunning(task)).thenReturn(true);
        when(agentClient.runEtl(task.getFilePath(), task.getFileType(), false))
                .thenThrow(new IllegalStateException("agent down"));

        assertThatThrownBy(() -> consumer.onTask(new EtlTaskMessage(9L), channel, amqpMessage))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("agent down");

        verify(taskService).markRunning(task);
        verify(taskService).fail(task, "agent down");
        verify(channel, never()).basicAck(anyLong(), eq(false));
    }

    @Test
    void duplicateRunningDeliveryCannotStartAnotherAttempt() throws Exception {
        EtlTask task = task(10L, EtlTaskService.STATUS_RUNNING);
        when(taskService.getById(10L)).thenReturn(task);
        when(taskService.markRunning(task)).thenReturn(false);
        consumer.onTask(new EtlTaskMessage(10L), channel, amqpMessage);
        verifyNoInteractions(agentClient);
        verify(channel).basicAck(123L, false);
    }

    @Test
    void duplicateBusinessFailureAndPartialResultAreTerminalUntilExplicitRetry() throws Exception {
        for (String status : new String[]{EtlTaskService.STATUS_FAILED, EtlTaskService.STATUS_PARTIAL}) {
            EtlTask task = task(11L, status);
            when(taskService.getById(11L)).thenReturn(task);
            consumer.onTask(new EtlTaskMessage(11L), channel, amqpMessage);
            verify(taskService, never()).markRunning(task);
        }
        verifyNoInteractions(agentClient);
    }

    @Test
    void unpersistedResultIsNeverAcked() throws Exception {
        EtlTask task = task(12L, EtlTaskService.STATUS_PENDING);
        when(taskService.getById(12L)).thenReturn(task);
        when(taskService.markRunning(task)).thenReturn(true);
        Map<String, Object> report = Map.of("status", "success");
        when(agentClient.runEtl(task.getFilePath(), task.getFileType(), false)).thenReturn(report);
        doThrow(new IllegalStateException("db offline")).when(taskService).complete(task, report);
        doThrow(new IllegalStateException("db still offline")).when(taskService).fail(task, "db offline");
        assertThatThrownBy(() -> consumer.onTask(new EtlTaskMessage(12L), channel, amqpMessage))
                .hasMessage("db offline");
        verify(channel, never()).basicAck(anyLong(), eq(false));
    }

    @Test
    void lostResponseKeepsRunningSoListenerRetryDoesNotResubmitPythonWork() throws Exception {
        EtlTask task = task(13L, EtlTaskService.STATUS_PENDING);
        when(taskService.getById(13L)).thenReturn(task);
        when(taskService.markRunning(task)).thenReturn(true, false);
        var uncertain = new EtlExecutionUncertainException(new java.util.concurrent.TimeoutException());
        when(agentClient.runEtl(task.getFilePath(), task.getFileType(), false)).thenThrow(uncertain);
        assertThatThrownBy(() -> consumer.onTask(new EtlTaskMessage(13L), channel, amqpMessage))
                .isInstanceOf(EtlExecutionUncertainException.class);
        verify(taskService).markUncertain(task, uncertain.getMessage());
        verify(taskService, never()).fail(task, uncertain.getMessage());
        consumer.onTask(new EtlTaskMessage(13L), channel, amqpMessage);
        verify(agentClient).runEtl(task.getFilePath(), task.getFileType(), false);
        verify(channel).basicAck(123L, false);
    }

    private static EtlTask task(long id, String status) {
        EtlTask task = new EtlTask();
        task.setId(id);
        task.setFilePath("D:/tmp/report.pdf");
        task.setFileType("financial");
        task.setStatus(status);
        return task;
    }
}
