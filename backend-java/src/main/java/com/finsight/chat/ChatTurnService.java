package com.finsight.chat;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.finsight.chat.entity.ChatSession;
import com.finsight.common.api.ErrorCode;
import com.finsight.common.exception.BizException;
import lombok.RequiredArgsConstructor;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.UUID;

/** 作品说明：持久化任务身份及终态归属独立于 SSE 连接。 */
@Service
@RequiredArgsConstructor
public class ChatTurnService {
    private final JdbcTemplate jdbc;
    private final ChatSessionService sessions;
    private final ObjectMapper json;

    public record Turn(String taskId, long userId, long sessionId, String sessionUid,
                       String clientRequestId, String question, String status, long deadline,
                       JsonNode result, boolean saved, String error) {
        public boolean terminal() { return List.of("completed", "cancelled", "failed").contains(status); }
    }

    private Turn row(java.sql.ResultSet rs, int index) throws java.sql.SQLException {
        JsonNode result = null;
        String raw = rs.getString("result");
        if (raw != null) {
            try { result = json.readTree(raw); }
            catch (Exception invalid) { throw new java.sql.SQLException("Invalid saved turn result"); }
        }
        return new Turn(rs.getString("task_id"), rs.getLong("user_id"), rs.getLong("session_id"),
                rs.getString("session_uid"), rs.getString("client_request_id"), rs.getString("question"),
                rs.getString("status"), rs.getLong("deadline_ms"), result,
                rs.getBoolean("saved"), rs.getString("error_code"));
    }

    public Turn byClient(long userId, String clientId) {
        if (clientId == null || clientId.isBlank()) return null;
        List<Turn> rows = jdbc.query("SELECT * FROM chat_turn WHERE user_id=? AND client_request_id=?", this::row, userId, clientId);
        return rows.isEmpty() ? null : rows.get(0);
    }

    public Turn owned(long userId, String taskId) {
        List<Turn> rows = jdbc.query("SELECT * FROM chat_turn WHERE user_id=? AND task_id=?", this::row, userId, taskId);
        if (rows.isEmpty()) throw new BizException(ErrorCode.NOT_FOUND, "任务不存在");
        Turn turn = rows.get(0);
        sessions.requireOwned(userId, turn.sessionUid());
        return turn;
    }

    public List<Turn> active(long userId, String sessionUid) {
        sessions.requireOwned(userId, sessionUid);
        return jdbc.query("SELECT * FROM chat_turn WHERE user_id=? AND session_uid=? AND active_slot=1", this::row, userId, sessionUid);
    }

    public List<Turn> recoverable() {
        return jdbc.query("SELECT * FROM chat_turn WHERE active_slot=1 ORDER BY created_at LIMIT 16", this::row);
    }

    @Transactional
    public Turn create(long userId, ChatSession session, String clientId, String question) {
        sessions.lockForTask(session);
        Turn existing = byClient(userId, clientId);
        if (existing != null) return existing;
        String taskId = UUID.randomUUID().toString();
        long deadline = System.currentTimeMillis() + 270_000;
        try {
            jdbc.update("INSERT INTO chat_turn(task_id,user_id,session_id,session_uid,client_request_id,question,status,deadline_ms) VALUES(?,?,?,?,?,?,'queued',?)",
                    taskId, userId, session.getId(), session.getSessionUid(), clientId, question, deadline);
        } catch (DuplicateKeyException conflict) {
            existing = byClient(userId, clientId);
            if (existing != null) return existing;
            throw new BizException(ErrorCode.CONFLICT, "这个会话已有执行中的任务");
        }
        return owned(userId, taskId);
    }

    public boolean claimDispatch(String id) {
        return jdbc.update("UPDATE chat_turn SET dispatch_started=TRUE WHERE task_id=? AND status='queued' AND dispatch_started=FALSE", id) == 1;
    }

    public void observeWorkerState(String id, String state) {
        if ("running".equals(state)) {
            jdbc.update("UPDATE chat_turn SET status='running' WHERE task_id=? AND status='queued'", id);
        }
    }

    public boolean requestCancel(long userId, String id) {
        owned(userId, id);
        return jdbc.update("UPDATE chat_turn SET status='cancelling' WHERE task_id=? AND user_id=? AND status IN ('queued','running')", id, userId) == 1;
    }

    /** 作品说明：消息、保存标记及终态同事务提交；已确认取消阻断迟到的完成结果。 */
    @Transactional
    public boolean finish(long userId, String id, ChatSession session, String content, String metadata,
                          JsonNode result, String target) {
        Turn turn = jdbc.query("SELECT * FROM chat_turn WHERE task_id=? AND user_id=? FOR UPDATE", this::row, id, userId)
                .stream().findFirst().orElseThrow(() -> new BizException(ErrorCode.NOT_FOUND, "任务不存在"));
        if (turn.terminal()) return false;
        if ("cancelling".equals(turn.status()) && "completed".equals(target)) return false;
        if (!List.of("completed", "cancelled", "failed").contains(target)) throw new IllegalArgumentException("Invalid terminal state");
        sessions.persistTurn(session, turn.question(), content, metadata);
        jdbc.update("UPDATE chat_turn SET status=?,result=?,saved=1,error_code=NULL WHERE task_id=?", target, result.toString(), id);
        return true;
    }

    /** 作品说明：消息保存回滚后，通过独立事务记录可见的保存失败。 */
    public void saveFailed(long userId, String id, JsonNode result) {
        jdbc.update("UPDATE chat_turn SET status='failed',result=?,saved=0,error_code='persistence_failed' WHERE user_id=? AND task_id=? AND status IN ('queued','running','cancelling')", result.toString(), userId, id);
    }
}
