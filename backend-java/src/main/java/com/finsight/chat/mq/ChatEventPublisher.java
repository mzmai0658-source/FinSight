package com.finsight.chat.mq;

import com.finsight.chat.entity.ChatLog;
import com.finsight.chat.mapper.ChatLogMapper;
import com.finsight.chat.mq.ChatMqMessages.ChatLogMessage;
import com.finsight.chat.mq.ChatMqMessages.TitleGenMessage;
import com.finsight.config.RabbitConfig;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.stereotype.Component;

/**
 * 作品说明：对话事件发布：主链路只负责 publish，落库/标题生成由消费者异步处理。
 * MQ 不可用时降级：审计直写 DB，标题保留截断兜底值。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class ChatEventPublisher {

    private final RabbitTemplate rabbitTemplate;
    private final ChatLogMapper chatLogMapper;

    public void publishChatLog(ChatLogMessage message) {
        try {
            rabbitTemplate.convertAndSend(RabbitConfig.EXCHANGE, RabbitConfig.CHAT_LOG_RK, message);
        } catch (Exception e) {
            log.warn("[mq] chat_log 发布失败，降级直写: {}", e.toString());
            insertDirectly(message);
        }
    }

    public void publishTitleTask(TitleGenMessage message) {
        try {
            rabbitTemplate.convertAndSend(RabbitConfig.EXCHANGE, RabbitConfig.CHAT_TITLE_RK, message);
        } catch (Exception e) {
            // 作品说明：标题任务失败时保留截断问题标题，不影响财务回答保存。
            log.warn("[mq] 标题任务发布失败（保留兜底标题）: {}", e.toString());
        }
    }

    private void insertDirectly(ChatLogMessage message) {
        try {
            ChatLog chatLog = new ChatLog();
            chatLog.setUserId(message.userId());
            chatLog.setSessionUid(message.sessionUid());
            chatLog.setRequestId(message.requestId() == null ? "" : message.requestId());
            chatLog.setQuestion(message.question());
            chatLog.setStatus(message.status());
            chatLog.setDurationMs(message.durationMs());
            chatLogMapper.insert(chatLog);
        } catch (Exception e) {
            log.error("[mq] chat_log 降级直写也失败: {}", e.toString());
        }
    }
}
