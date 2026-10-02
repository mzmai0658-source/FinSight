package com.finsight.chat.mq;

import com.finsight.chat.AgentClient;
import com.finsight.chat.ChatSessionService;
import com.finsight.chat.mq.ChatMqMessages.TitleGenMessage;
import com.finsight.common.mq.ManualAckSupport;
import com.finsight.config.RabbitConfig;
import com.rabbitmq.client.Channel;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.stereotype.Component;

/** 作品说明：异步调用 Python 生成会话标题，失败按监听器规则重试并进入死信；会话保留截断问题作为兜底标题。 */
@Slf4j
@Component
@RequiredArgsConstructor
public class TitleGenConsumer {

    private final AgentClient agentClient;
    private final ChatSessionService sessionService;

    @RabbitListener(queues = RabbitConfig.CHAT_TITLE_QUEUE)
    public void onTitleTask(TitleGenMessage message, Channel channel, Message amqpMessage) {
        String title = agentClient.generateTitle(message.question(), message.answerSnippet());
        if (title == null || title.isBlank()) {
            log.info("[mq] 标题生成为空，保留兜底标题 session={}", message.sessionUid());
            ManualAckSupport.ack(channel, amqpMessage);
            return;
        }
        sessionService.updateTitle(message.sessionId(), title);
        ManualAckSupport.ack(channel, amqpMessage);
        log.info("[mq] 会话标题已更新 session={} title={}", message.sessionUid(), title);
    }
}
