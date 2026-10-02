package com.finsight.etl.mq;

/** 作品说明：ETL 任务消息：消费者按 taskId 取任务详情执行 */
public record EtlTaskMessage(long taskId) {
}
