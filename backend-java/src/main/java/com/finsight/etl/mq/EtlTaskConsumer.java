package com.finsight.etl.mq;

import com.finsight.chat.AgentClient;
import com.finsight.common.metrics.ObservabilityService;
import com.finsight.common.mq.ManualAckSupport;
import com.finsight.config.RabbitConfig;
import com.finsight.etl.EtlTaskService;
import com.finsight.etl.EtlExecutionUncertainException;
import com.finsight.etl.entity.EtlTask;
import com.rabbitmq.client.Channel;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;

import java.util.Map;
import java.util.Set;

/**
 * 作品说明：ETL 任务消费者：串行（concurrency=1）调用 Python 单文件管线，回写状态机。
 * 管线内部 LLM 抽取已有并发，任务级再并发会撞 API 限速。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class EtlTaskConsumer {

    private final EtlTaskService taskService;
    private final AgentClient agentClient;
    private final StringRedisTemplate redis;
    private final ObservabilityService observabilityService;

    /** 作品说明：是否在管线中同步做 RAG 入库（依赖向量服务可用，默认关闭） */
    @Value("${finsight.etl.ingest-rag:false}")
    private boolean ingestRag;

    @RabbitListener(queues = RabbitConfig.ETL_TASK_QUEUE, concurrency = "1")
    public void onTask(EtlTaskMessage message, Channel channel, Message amqpMessage) {
        EtlTask task = taskService.getById(message.taskId());
        if (task == null) {
            log.warn("[etl] 任务不存在，丢弃: {}", message.taskId());
            ManualAckSupport.ack(channel, amqpMessage);
            return;
        }
        if (EtlTaskService.STATUS_SUCCESS.equals(task.getStatus())
                || EtlTaskService.STATUS_PARTIAL.equals(task.getStatus())
                || (EtlTaskService.STATUS_FAILED.equals(task.getStatus()) && !Boolean.TRUE.equals(task.getRetryable()))) {
            ManualAckSupport.ack(channel, amqpMessage);
            return; // 作品说明：重复投递保护
        }

        long startedAt = System.currentTimeMillis();
        if (!taskService.markRunning(task)) {
            // 作品说明：任务已由其他消费者持久占用，遗留执行状态由恢复流程处理。
            ManualAckSupport.ack(channel, amqpMessage);
            return;
        }
        log.info("[etl] 开始执行任务 taskId={} file={}", task.getId(), task.getFileName());
        try {
            Map<String, Object> report = agentClient.runEtl(task.getFilePath(), task.getFileType(), ingestRag);
            taskService.complete(task, report);
            observabilityService.recordEtlTask(task.getStatus(), System.currentTimeMillis() - startedAt);
            observabilityService.recordMqEtl(task.getStatus());
            if (EtlTaskService.STATUS_SUCCESS.equals(task.getStatus()) || EtlTaskService.STATUS_PARTIAL.equals(task.getStatus())) {
                evictMarketCaches();
            }
            log.info("[etl] 任务完成 taskId={} status={} message={}",
                    task.getId(), task.getStatus(), task.getMessage());
        } catch (Exception e) {
            // 作品说明：先保存可见状态，再抛出异常，交由监听器重试与死信机制恢复。
            log.error("[etl] 任务执行失败 taskId={}: {}", task.getId(), e.toString());
            try {
                if (e instanceof EtlExecutionUncertainException) {
                    taskService.markUncertain(task, e.getMessage());
                } else {
                    taskService.fail(task, e.getMessage());
                }
            } catch (Exception persistenceFailure) {
                e.addSuppressed(persistenceFailure);
                log.error("[etl] failed to persist task failure taskId={}", task.getId(), persistenceFailure);
            }
            String outcome = e instanceof EtlExecutionUncertainException ? EtlTaskService.STATUS_RUNNING : EtlTaskService.STATUS_FAILED;
            observabilityService.recordEtlTask(outcome, System.currentTimeMillis() - startedAt);
            observabilityService.recordMqEtl(outcome);
            if (e instanceof RuntimeException runtimeException) {
                throw runtimeException;
            }
            throw new IllegalStateException(e);
        }
        ManualAckSupport.ack(channel, amqpMessage);
    }

    /** 作品说明：新财务数据入库后失效行情缓存，门户立刻可见 */
    private void evictMarketCaches() {
        try {
            Set<String> keys = redis.keys("market:companies*");
            if (keys != null && !keys.isEmpty()) {
                redis.delete(keys);
            }
            redis.delete("market:summary");
            Set<String> detailKeys = redis.keys("market:detail:*");
            if (detailKeys != null && !detailKeys.isEmpty()) {
                redis.delete(detailKeys);
            }
            Set<String> seriesKeys = redis.keys("market:series:*");
            if (seriesKeys != null && !seriesKeys.isEmpty()) {
                redis.delete(seriesKeys);
            }
        } catch (Exception e) {
            log.warn("[etl] 行情缓存失效失败（缓存将按 TTL 自然过期）: {}", e.toString());
        }
    }
}
