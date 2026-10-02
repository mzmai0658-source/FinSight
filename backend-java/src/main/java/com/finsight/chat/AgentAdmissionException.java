package com.finsight.chat;

/** 作品说明：工作线程创建任务前的明确拒绝，与传输连接故障分开记录。 */
public final class AgentAdmissionException extends RuntimeException {
    public AgentAdmissionException() { super("model_queue_full"); }
    public String reasonCode() { return "model_queue_full"; }
    public String userMessage() { return "服务当前忙碌，等待队列已满，本轮未开始，请稍后重试。"; }
}
