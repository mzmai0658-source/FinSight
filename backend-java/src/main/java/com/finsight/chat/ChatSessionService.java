package com.finsight.chat;

import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.finsight.chat.dto.ChatDtos.MessageView;
import com.finsight.chat.dto.ChatDtos.SessionDetail;
import com.finsight.chat.dto.ChatDtos.SessionSummary;
import com.finsight.chat.entity.ChatMessage;
import com.finsight.chat.entity.ChatSession;
import com.finsight.chat.mapper.ChatMessageMapper;
import com.finsight.chat.mapper.ChatSessionMapper;
import com.finsight.common.api.ErrorCode;
import com.finsight.common.exception.BizException;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;

@Slf4j
@Service
@RequiredArgsConstructor
public class ChatSessionService {

    /** 作品说明：送往 Agent 的历史条数上限（Python 侧还会做二次截断） */
    private static final int HISTORY_LIMIT = 20;
    private static final int TITLE_LIMIT = 30;
    private static final int MAX_SESSIONS_PER_USER = 100;

    private final ChatSessionMapper sessionMapper;
    private final ChatMessageMapper messageMapper;
    private final ObjectMapper objectMapper;
    private final AgentClient agentClient;

    @Transactional
    public ChatSession create(long userId) {
        prune(userId);
        ChatSession session = new ChatSession();
        session.setSessionUid(UUID.randomUUID().toString());
        session.setUserId(userId);
        session.setTitle("新对话");
        session.setMessageCount(0);
        sessionMapper.insert(session);
        return session;
    }

    public List<SessionSummary> list(long userId) {
        return sessionMapper.selectList(Wrappers.<ChatSession>lambdaQuery()
                        .eq(ChatSession::getUserId, userId)
                        .orderByDesc(ChatSession::getUpdatedAt)
                        .last("LIMIT " + MAX_SESSIONS_PER_USER))
                .stream()
                .map(this::toSummary)
                .toList();
    }

    /** 作品说明：校验归属：会话必须属于当前用户 */
    public ChatSession requireOwned(long userId, String sessionUid) {
        ChatSession session = sessionMapper.selectOne(Wrappers.<ChatSession>lambdaQuery()
                .eq(ChatSession::getSessionUid, sessionUid)
                .eq(ChatSession::getUserId, userId));
        if (session == null) {
            throw new BizException(ErrorCode.NOT_FOUND, "会话不存在或已删除");
        }
        return session;
    }

    public SessionDetail detail(long userId, String sessionUid) {
        ChatSession session = requireOwned(userId, sessionUid);
        List<MessageView> messages = messageMapper.selectList(Wrappers.<ChatMessage>lambdaQuery()
                        .eq(ChatMessage::getSessionId, session.getId())
                        .orderByAsc(ChatMessage::getId))
                .stream()
                .map(this::toView)
                .toList();
        return new SessionDetail(session.getSessionUid(), session.getTitle(), session.getMessageCount(),
                session.getCreatedAt(), session.getUpdatedAt(), messages);
    }

    @Transactional
    public void delete(long userId, String sessionUid) {
        ChatSession session = requireOwned(userId, sessionUid);
        sessionMapper.lockExisting(session.getId());
        if (sessionMapper.activeTurns(session.getId()) > 0)
            throw new BizException(ErrorCode.CONFLICT, "会话任务仍在执行，请先停止并等待确认后删除");
        sessionMapper.deleteById(session.getId());
        // 作品说明：消息物理删除（会话已软删，消息无单独保留价值）
        messageMapper.delete(Wrappers.<ChatMessage>lambdaQuery().eq(ChatMessage::getSessionId, session.getId()));
        // 作品说明：异步清理 Python 侧图表文件
        agentClient.deleteSessionCharts(sessionUid);
    }

    /** 作品说明：向 Agent 传递会话正文与服务端保存的结构化对话状态。 */
    public List<Map<String, Object>> buildHistory(long sessionId) {
        List<ChatMessage> recent = messageMapper.selectList(Wrappers.<ChatMessage>lambdaQuery()
                .eq(ChatMessage::getSessionId, sessionId)
                .orderByDesc(ChatMessage::getId)
                .last("LIMIT " + HISTORY_LIMIT));
        List<Map<String, Object>> history = new ArrayList<>(recent.size());
        for (int i = recent.size() - 1; i >= 0; i--) {
            ChatMessage message = recent.get(i);
            Map<String, Object> entry = new java.util.HashMap<>();
            entry.put("role", message.getRole());
            entry.put("content", message.getContent());
            if (ChatMessage.ROLE_ASSISTANT.equals(message.getRole()) && message.getMetadata() != null) {
                try {
                    JsonNode source = objectMapper.readTree(message.getMetadata());
                    Map<String, Object> metadata = new java.util.HashMap<>();
                    for (String field : new String[]{"dialogue_state", "response_kind"}) {
                        if (source.hasNonNull(field)) metadata.put(field, source.get(field));
                    }
                    if (!metadata.isEmpty()) entry.put("metadata", metadata);
                } catch (Exception invalidMetadata) {
                    log.warn("历史对话状态解析失败 id={}", message.getId());
                }
            }
            history.add(entry);
        }
        return history;
    }

    /**
     * 作品说明：一轮对话落库：用户消息 + 助手消息 + 会话统计，单事务保证一致性。
     * 
     * @return 是否为该会话首轮（首轮触发异步标题生成）
     */
    @Transactional
    public boolean persistTurn(ChatSession session, String question, String assistantContent, String metadataJson) {
        boolean firstTurn = session.getMessageCount() == null || session.getMessageCount() == 0;

        ChatMessage userMessage = new ChatMessage();
        userMessage.setSessionId(session.getId());
        userMessage.setRole(ChatMessage.ROLE_USER);
        userMessage.setContent(question);
        if (messageMapper.insert(userMessage) != 1) throw new IllegalStateException("User message was not saved");

        ChatMessage assistantMessage = new ChatMessage();
        assistantMessage.setSessionId(session.getId());
        assistantMessage.setRole(ChatMessage.ROLE_ASSISTANT);
        assistantMessage.setContent(assistantContent);
        assistantMessage.setMetadata(metadataJson);
        if (messageMapper.insert(assistantMessage) != 1) throw new IllegalStateException("Assistant message was not saved");

        ChatSession update = new ChatSession();
        update.setId(session.getId());
        update.setMessageCount((session.getMessageCount() == null ? 0 : session.getMessageCount()) + 2);
        if (firstTurn) {
            update.setTitle(truncateTitle(question));
        }
        if (sessionMapper.updateById(update) != 1) throw new IllegalStateException("Session was not updated");
        return firstTurn;
    }

    public void updateTitle(long sessionId, String title) {
        ChatSession update = new ChatSession();
        update.setId(sessionId);
        update.setTitle(truncateTitle(title));
        sessionMapper.updateById(update);
    }

    private String truncateTitle(String raw) {
        String title = raw == null ? "" : raw.strip().replaceAll("\\s+", " ");
        if (title.isEmpty()) {
            return "新对话";
        }
        return title.length() > TITLE_LIMIT ? title.substring(0, TITLE_LIMIT) : title;
    }

    /** 作品说明：超出上限时清理最久未活跃的会话 */
    private void prune(long userId) {
        Long count = sessionMapper.selectCount(Wrappers.<ChatSession>lambdaQuery()
                .eq(ChatSession::getUserId, userId));
        if (count == null || count < MAX_SESSIONS_PER_USER) {
            return;
        }
        List<ChatSession> oldest = sessionMapper.selectList(Wrappers.<ChatSession>lambdaQuery()
                .eq(ChatSession::getUserId, userId)
                .orderByAsc(ChatSession::getUpdatedAt)
                .last("LIMIT " + (count - MAX_SESSIONS_PER_USER + 1)));
        for (ChatSession session : oldest) {
            if (sessionMapper.lockExisting(session.getId()) == null) continue;
            if (sessionMapper.activeTurns(session.getId()) > 0) continue;
            sessionMapper.deleteById(session.getId());
            messageMapper.delete(Wrappers.<ChatMessage>lambdaQuery().eq(ChatMessage::getSessionId, session.getId()));
        }
    }

    public void lockForTask(ChatSession session) {
        if (sessionMapper.lockExisting(session.getId()) == null)
            throw new BizException(ErrorCode.NOT_FOUND, "会话不存在或已删除");
    }

    private SessionSummary toSummary(ChatSession session) {
        return new SessionSummary(session.getSessionUid(), session.getTitle(), session.getMessageCount(),
                session.getCreatedAt(), session.getUpdatedAt());
    }

    private MessageView toView(ChatMessage message) {
        JsonNode metadata = null;
        if (message.getMetadata() != null && !message.getMetadata().isBlank()) {
            try {
                metadata = objectMapper.readTree(message.getMetadata());
            } catch (Exception e) {
                log.warn("消息 metadata 解析失败 id={}", message.getId());
            }
        }
        return new MessageView(message.getId(), message.getRole(), message.getContent(), metadata,
                message.getCreatedAt());
    }
}
