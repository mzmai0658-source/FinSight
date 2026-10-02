package com.finsight.chat.mq;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.finsight.chat.entity.ChatLog;
import com.finsight.chat.mapper.ChatLogMapper;
import com.finsight.chat.mq.ChatMqMessages.ChatLogMessage;
import com.finsight.common.mq.ManualAckSupport;
import com.finsight.config.RabbitConfig;
import com.rabbitmq.client.Channel;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.stereotype.Component;

/** 作品说明：对话审计异步落库（失败自动重试，重试耗尽进死信队列） */
@Slf4j
@Component
@RequiredArgsConstructor
public class ChatLogConsumer {

    private final ChatLogMapper chatLogMapper;

    @RabbitListener(queues = RabbitConfig.CHAT_LOG_QUEUE)
    public void onChatLog(ChatLogMessage message, Channel channel, Message amqpMessage) {
        String requestId = message.requestId() == null ? "" : message.requestId();
        if (!requestId.isBlank()) {
            Long count = chatLogMapper.selectCount(new LambdaQueryWrapper<ChatLog>()
                    .eq(ChatLog::getRequestId, requestId));
            if (count != null && count > 0) {
                ManualAckSupport.ack(channel, amqpMessage);
                return;
            }
        }
        ChatLog chatLog = new ChatLog();
        chatLog.setUserId(message.userId());
        chatLog.setSessionUid(message.sessionUid());
        chatLog.setRequestId(requestId);
        chatLog.setQuestion(message.question());
        chatLog.setStatus(message.status());
        chatLog.setDurationMs(message.durationMs());
        chatLogMapper.insert(chatLog);
        ManualAckSupport.ack(channel, amqpMessage);
        log.debug("[mq] chat_log 已落库 userId={} session={}", message.userId(), message.sessionUid());
    }
}
