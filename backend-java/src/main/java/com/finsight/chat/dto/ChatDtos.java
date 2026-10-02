package com.finsight.chat.dto;

import com.fasterxml.jackson.databind.JsonNode;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import com.finsight.common.validation.QuestionText;

import java.time.LocalDateTime;
import java.util.List;

public final class ChatDtos {

    private ChatDtos() {
    }

    public record StreamRequest(
            @QuestionText String question,
            String sessionUid,
            /** 作品说明：客户端幂等 ID，重复提交防重 */
            @Size(max = 128) String clientRequestId) {
        public StreamRequest {
            question = QuestionText.Validator.normalize(question);
        }
    }

    public record SessionSummary(String sessionUid, String title, int messageCount,
                                 LocalDateTime createdAt, LocalDateTime updatedAt) {
    }

    public record MessageView(long id, String role, String content, JsonNode metadata,
                              LocalDateTime createdAt) {
    }

    public record SessionDetail(String sessionUid, String title, int messageCount,
                                LocalDateTime createdAt, LocalDateTime updatedAt,
                                List<MessageView> messages) {
    }
}
