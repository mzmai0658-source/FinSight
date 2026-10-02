package com.finsight.etl;

import com.finsight.common.exception.BizException;
import com.finsight.config.RabbitConfig;
import com.finsight.common.mq.MqOutboxService;
import com.finsight.etl.entity.EtlTask;
import com.finsight.etl.mapper.EtlTaskMapper;
import com.finsight.etl.mq.EtlTaskMessage;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class EtlTaskServiceTest {

    @Mock
    private EtlTaskMapper taskMapper;
    @Mock
    private MqOutboxService mqOutboxService;
    @Mock
    private ObjectMapper objectMapper;

    @InjectMocks
    private EtlTaskService service;

    @Test
    void partialCompletionRetainsRetryableStagesAndState() throws Exception {
        EtlTask task = new EtlTask(); task.setId(45L); task.setRunToken("current-attempt");
        when(taskMapper.update(eq(task), any())).thenReturn(1);
        var steps = List.of(Map.of("name", "rag", "status", "failed"));
        var retryable = List.of("rag");
        service.complete(task, Map.of("status", "partial", "steps", steps, "retryable_steps", retryable));
        assertThat(task.getStatus()).isEqualTo(EtlTaskService.STATUS_PARTIAL);
        verify(objectMapper).writeValueAsString(Map.of("steps", steps, "retryable_steps", retryable));
        verify(taskMapper).update(eq(task), any());
    }

    @Test
    void runningTaskCannotBeRequeuedConcurrently() {
        EtlTask task = new EtlTask(); task.setId(46L); task.setStatus(EtlTaskService.STATUS_RUNNING);
        when(taskMapper.selectById(46L)).thenReturn(task);
        assertThatThrownBy(() -> service.retry(46L)).isInstanceOf(BizException.class);
        verify(taskMapper, never()).updateById(task);
    }

    @Test
    void retryFailedTaskResetsStateAndRequeuesByTaskId() {
        EtlTask task = new EtlTask();
        task.setId(42L);
        task.setStatus(EtlTaskService.STATUS_FAILED);
        task.setMessage("agent timeout");
        task.setStartedAt(LocalDateTime.now().minusMinutes(5));
        task.setFinishedAt(LocalDateTime.now().minusMinutes(1));
        when(taskMapper.selectById(42L)).thenReturn(task);
        when(taskMapper.prepareRetry(42L)).thenReturn(1);

        EtlTask retried = service.retry(42L);

        assertThat(retried.getStatus()).isEqualTo(EtlTaskService.STATUS_PENDING);
        assertThat(retried.getMessage()).isEmpty();
        assertThat(retried.getStartedAt()).isNull();
        assertThat(retried.getFinishedAt()).isNull();
        verify(taskMapper).prepareRetry(42L);
        verify(mqOutboxService).publish(
                RabbitConfig.EXCHANGE,
                RabbitConfig.ETL_TASK_RK,
                new EtlTaskMessage(42L));
    }

    @Test
    void retrySuccessfulTaskIsRejectedToKeepTaskIdIdempotent() {
        EtlTask task = new EtlTask();
        task.setId(43L);
        task.setStatus(EtlTaskService.STATUS_SUCCESS);
        when(taskMapper.selectById(43L)).thenReturn(task);

        assertThatThrownBy(() -> service.retry(43L))
                .isInstanceOf(BizException.class);

        verify(taskMapper, never()).updateById(task);
        verify(mqOutboxService, never()).publish(
                RabbitConfig.EXCHANGE,
                RabbitConfig.ETL_TASK_RK,
                new EtlTaskMessage(43L));
    }

    @Test
    void recoverStaleRunningTasksMarksTimedOutTasksFailed() {
        EtlTask task = new EtlTask();
        task.setId(44L);
        task.setStatus(EtlTaskService.STATUS_RUNNING);
        task.setStartedAt(LocalDateTime.now().minusMinutes(30));
        when(taskMapper.selectList(any())).thenReturn(List.of(task));
        when(taskMapper.update(eq(task), any())).thenReturn(1);

        int recovered = service.recoverStaleRunningTasks(Duration.ofMinutes(15), 20);

        assertThat(recovered).isEqualTo(1);
        assertThat(task.getStatus()).isEqualTo(EtlTaskService.STATUS_FAILED);
        assertThat(task.getMessage()).contains("timed out");
        assertThat(task.getFinishedAt()).isNotNull();
        verify(taskMapper).update(eq(task), any());
    }

    @Test
    void concurrentRetryLoserDoesNotEnqueueAnotherMessage() {
        EtlTask task = new EtlTask(); task.setId(50L); task.setStatus(EtlTaskService.STATUS_FAILED);
        when(taskMapper.selectById(50L)).thenReturn(task);
        when(taskMapper.prepareRetry(50L)).thenReturn(0);
        assertThatThrownBy(() -> service.retry(50L)).hasMessageContaining("changed or was not saved");
        verify(mqOutboxService, never()).publish(anyString(), anyString(), any());
    }

    @Test
    void claimMustSucceedAtomicallyBeforeRunning() {
        EtlTask task = new EtlTask(); task.setId(51L); task.setStatus(EtlTaskService.STATUS_PENDING);
        when(taskMapper.claim(eq(51L), anyString(), any())).thenReturn(0, 1);
        assertThat(service.markRunning(task)).isFalse();
        assertThat(task.getRunToken()).isNull();
        assertThat(service.markRunning(task)).isTrue();
        assertThat(task.getRunToken()).isNotBlank();
        assertThat(task.getAttemptCount()).isEqualTo(1);
    }

    @Test
    void staleAttemptCannotOverwriteAReplacementRun() {
        EtlTask task = new EtlTask(); task.setId(52L); task.setRunToken("old-attempt");
        when(taskMapper.update(eq(task), any())).thenReturn(0);
        assertThatThrownBy(() -> service.complete(task, Map.of("status", "success")))
                .hasMessageContaining("persist current ETL attempt");
    }

    @Test
    void recoveryCountsOnlyRowsStillRunningAtUpdateTime() {
        EtlTask task = new EtlTask(); task.setId(53L); task.setRunToken("attempt");
        when(taskMapper.selectList(any())).thenReturn(List.of(task));
        when(taskMapper.update(eq(task), any())).thenReturn(0);
        assertThat(service.recoverStaleRunningTasks(Duration.ofHours(3), 20)).isZero();
    }
}
