package com.finsight.chat.mq;

public final class ChatMqMessages {

    private ChatMqMessages() {
    }

    /** 作品说明：对话审计消息 */
    public record ChatLogMessage(
            long userId,
            String sessionUid,
            String requestId,
            String question,
            String status,
            int durationMs) {
    }

    /** 作品说明：会话标题生成任务 */
    public record TitleGenMessage(
            long sessionId,
            String sessionUid,
            String question,
            String answerSnippet) {
    }
}
