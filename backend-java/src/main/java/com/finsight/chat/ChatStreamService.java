package com.finsight.chat;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.finsight.chat.dto.ChatDtos.StreamRequest;
import com.finsight.chat.entity.ChatSession;
import com.finsight.chat.mq.ChatEventPublisher;
import com.finsight.chat.mq.ChatMqMessages.ChatLogMessage;
import com.finsight.chat.mq.ChatMqMessages.TitleGenMessage;
import com.finsight.common.api.ErrorCode;
import com.finsight.common.exception.BizException;
import com.finsight.common.metrics.ObservabilityService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.stereotype.Service;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;
import reactor.core.Disposable;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

import java.io.IOException;
import java.time.Duration;
import java.util.HashMap;
import java.util.Map;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.ConcurrentHashMap;
import org.springframework.scheduling.annotation.Scheduled;
import jakarta.annotation.PreDestroy;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** 作品说明：鉴权后的 SSE 请求组织会话占用、历史、Python 事件、保存与审计。刷新或断网不取消后台任务；停止生成通过独立取消接口处理，终态与保存由持久化任务记录决定。 */
@Slf4j
@Service
@RequiredArgsConstructor
public class ChatStreamService {

    private static final String TURN_LOCK_KEY = "chat:turn:%s";
    private static final String IDEM_KEY = "chat:idem:%d:%s";
    private static final Duration TURN_LOCK_TTL = Duration.ofSeconds(320);
    private static final Duration IDEM_TTL = Duration.ofMinutes(10);
    private static final Duration TURN_DEADLINE = Duration.ofSeconds(270);
    private static final DefaultRedisScript<Long> RELEASE_TURN = new DefaultRedisScript<>(
            "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end", Long.class);

    private final AgentClient agentClient;
    private final ChatSessionService sessionService;
    private final ChatEventPublisher eventPublisher;
    private final StringRedisTemplate redisTemplate;
    private final ObjectMapper objectMapper;
    private final ObservabilityService observabilityService;
    private final ChatTurnService turnService;
    private final Map<String, LiveTurn> liveTurns = new ConcurrentHashMap<>();
    private final ExecutorService recoveryWorkers = Executors.newFixedThreadPool(4, runnable -> {
        Thread thread = new Thread(runnable, "chat-task-recovery"); thread.setDaemon(true); return thread;
    });
    private final java.util.Set<String> recovering = ConcurrentHashMap.newKeySet();

    private record LiveTurn(ChatSession session, SseEmitter emitter, AtomicBoolean clientGone,
                            AtomicBoolean doneReceived, String lockKey, String lockToken) {}

    public record StreamContext(ChatSession session, SseEmitter emitter) {
    }

    public StreamContext stream(long userId, StreamRequest request, String traceId) {
        String clientId = request.clientRequestId() == null || request.clientRequestId().isBlank()
                ? UUID.randomUUID().toString() : request.clientRequestId().strip();
        ChatTurnService.Turn existing = turnService.byClient(userId, clientId);
        if (existing != null) {
            if (!existing.question().equals(request.question()) || request.sessionUid() != null && !request.sessionUid().isBlank() && !existing.sessionUid().equals(request.sessionUid()))
                throw new BizException(ErrorCode.CONFLICT, "同一请求编号不能用于不同问题或会话");
            return restoreStream(userId, existing);
        }

        ChatSession session = (request.sessionUid() == null || request.sessionUid().isBlank())
                ? sessionService.create(userId)
                : sessionService.requireOwned(userId, request.sessionUid());
        ChatTurnService.Turn turn = turnService.create(userId, session, clientId, request.question());
        if (!turnService.claimDispatch(turn.taskId())) return restoreStream(userId, turnService.owned(userId, turn.taskId()));

        // 作品说明：会话级并发锁：同一会话同时只允许一轮生成
        String lockKey = TURN_LOCK_KEY.formatted(session.getSessionUid());
        String lockToken = UUID.randomUUID().toString();
        // 作品说明：数据库唯一活跃任务约束保证会话并发正确性。
        // 作品说明：Redis 只作短期缓存，不能使已创建任务失去可恢复的持久身份。
        try { redisTemplate.opsForValue().setIfAbsent(lockKey, lockToken, TURN_LOCK_TTL); }
        catch (RuntimeException cacheUnavailable) { log.warn("[chat] lock cache unavailable"); }

        SseEmitter emitter = new SseEmitter(0L);
        AtomicBoolean clientGone = new AtomicBoolean(false);
        AtomicBoolean metricRecorded = new AtomicBoolean(false);
        AtomicBoolean activeReleased = new AtomicBoolean(false);
        AtomicBoolean doneReceived = new AtomicBoolean(false);
        long startedAt = System.currentTimeMillis();
        observabilityService.incrementActiveSse();

        Map<String, Object> payload = new HashMap<>();
        payload.put("question", request.question());
        try {
            payload.put("history", sessionService.buildHistory(session.getId()));
        } catch (RuntimeException historyFailure) {
            turnService.saveFailed(userId, turn.taskId(), failureRoot(request.question(), "会话上下文读取失败，本轮未开始。", "history_failed").get("result"));
            releaseTurnLock(lockKey, lockToken);
            releaseActiveSse(activeReleased);
            throw historyFailure;
        }
        payload.put("session_uid", session.getSessionUid());
        payload.put("version", 3);
        payload.put("task_id", turn.taskId());
        payload.put("client_request_id", clientId);
        payload.put("deadline", turn.deadline());
        liveTurns.put(turn.taskId(), new LiveTurn(session, emitter, clientGone, doneReceived, lockKey, lockToken));

        sendSafely(emitter, clientGone, "session", sessionEvent(turn).toString());

        Disposable subscription = Flux.defer(() -> agentClient.streamChat(payload, traceId))
                .takeUntilOther(Mono.delay(TURN_DEADLINE)
                        .flatMap(tick -> Mono.error(new java.util.concurrent.TimeoutException("Agent turn deadline exceeded"))))
                .subscribe(
                        event -> handleEvent(event, session, request, userId, traceId, startedAt, turn.taskId(),
                                emitter, clientGone, metricRecorded, doneReceived),
                        error -> {
                            log.warn("[chat] 上游流异常 session={} traceId={}: {}",
                                    session.getSessionUid(), traceId, error.toString());
                            if (error instanceof AgentAdmissionException refusal) {
                                ObjectNode root = failureRoot(request.question(), refusal.userMessage(), refusal.reasonCode());
                                ObjectNode result = (ObjectNode) root.get("result");
                                result.put("task_id", turn.taskId()); result.put("task_status", "failed");
                                clearFailureFacts(result, session);
                                handleEvent(ServerSentEvent.<String>builder(root.toString()).event("done").build(),
                                        session, request, userId, traceId, startedAt, turn.taskId(),
                                        emitter, clientGone, metricRecorded, doneReceived);
                            } else {
                                completeFailure("AI 分析服务连接失败，本轮未完成，请重试。", session, request,
                                        userId, traceId, startedAt, turn.taskId(), emitter, clientGone, metricRecorded, doneReceived);
                            }
                            releaseTurnLock(lockKey, lockToken);
                            releaseActiveSse(activeReleased);
                            liveTurns.remove(turn.taskId());
                            emitter.complete();
                        },
                        () -> {
                            completeFailure("连接已结束，但未收到完整回答，请重试。", session, request,
                                    userId, traceId, startedAt, turn.taskId(), emitter, clientGone, metricRecorded, doneReceived);
                            releaseTurnLock(lockKey, lockToken);
                            releaseActiveSse(activeReleased);
                            liveTurns.remove(turn.taskId());
                            emitter.complete();
                        });

        // 作品说明：客户端断开不取消上游订阅：让本轮跑完以保证落库；上游时长由 WebClient responseTimeout 兜底
        emitter.onError(throwable -> {
            clientGone.set(true);
            releaseActiveSse(activeReleased);
        });
        emitter.onTimeout(() -> {
            clientGone.set(true);
            releaseActiveSse(activeReleased);
        });
        // 作品说明：进程关闭兜底
        emitter.onCompletion(() -> {
            releaseActiveSse(activeReleased);
            if (subscription.isDisposed()) {
                releaseTurnLock(lockKey, lockToken);
            }
        });
        return new StreamContext(session, emitter);
    }

    private ObjectNode sessionEvent(ChatTurnService.Turn turn) {
        ObjectNode event = objectMapper.createObjectNode();
        event.put("session_uid", turn.sessionUid());
        event.put("task_id", turn.taskId());
        event.put("client_request_id", turn.clientRequestId());
        event.put("task_status", turn.status());
        event.put("deadline", turn.deadline());
        event.put("version", 3);
        return event;
    }

    private StreamContext restoreStream(long userId, ChatTurnService.Turn turn) {
        ChatSession session = sessionService.requireOwned(userId, turn.sessionUid());
        SseEmitter emitter = new SseEmitter(0L);
        AtomicBoolean clientGone = new AtomicBoolean(false);
        sendSafely(emitter, clientGone, "session", sessionEvent(turn).toString());
        if (turn.terminal() && turn.result() != null) {
            ObjectNode root = objectMapper.createObjectNode();
            root.set("result", turn.result());
            root.put("session_uid", turn.sessionUid());
            root.put("persistence_status", turn.saved() ? "saved" : "failed");
            sendSafely(emitter, clientGone, "done", root.toString());
        }
        emitter.complete();
        return new StreamContext(session, emitter);
    }

    public ChatTurnService.Turn status(long userId, String taskId) {
        ChatTurnService.Turn turn = turnService.owned(userId, taskId);
        if (turn.terminal()) return turn;
        if (System.currentTimeMillis() > turn.deadline()) {
            stopWorker(taskId, turn.sessionUid());
            ObjectNode result = (ObjectNode) failureRoot(turn.question(), "服务时限已到，本轮未完成。", "turn_deadline_exceeded").get("result");
            if ("cancelling".equals(turn.status())) result.withObject("/answer").put("content", "本轮已超时，停止尚未取得确认；本轮结果不再发布。");
            result.put("task_id", taskId); result.put("task_status", "failed");
            try {
                ChatSession session = sessionService.requireOwned(userId, turn.sessionUid());
                clearFailureFacts(result, session);
                turnService.finish(userId, taskId, session, result.path("answer").path("content").asText(), buildMetadata(result), result,
                        "failed");
            } catch (Exception saveFailure) { turnService.saveFailed(userId, taskId, result); }
            return turnService.owned(userId, taskId);
        }
        try {
            Map<String, Object> worker = agentClient.taskStatus(taskId, turn.sessionUid()).block(Duration.ofSeconds(6));
            String workerStatus = worker == null ? "" : String.valueOf(worker.get("task_status"));
            turnService.observeWorkerState(taskId, workerStatus);
            if ("cancelling".equals(turn.status())) {
                return cancel(userId, taskId);
            }
            if ("completed".equals(workerStatus) && worker.get("result") != null) {
                JsonNode result = objectMapper.valueToTree(worker.get("result"));
                requireResultIdentity(result, taskId);
                ChatSession session = sessionService.requireOwned(userId, turn.sessionUid());
                turnService.finish(userId, taskId, session, result.path("answer").path("content").asText(),
                        buildMetadata(result), result, isFailedResult(result) ? "failed" : "completed");
                agentClient.acknowledgeSaved(taskId, turn.sessionUid());
            } else if ("failed".equals(workerStatus) || "cancelled".equals(workerStatus)) {
                JsonNode stored=worker.get("result")==null ? null : objectMapper.valueToTree(worker.get("result"));
                ObjectNode result;
                if (stored!=null && stored.isObject()) {
                    requireResultIdentity(stored,taskId);
                    result=((ObjectNode)stored).deepCopy();
                } else result=(ObjectNode)failureRoot(turn.question(), "任务已结束，本轮未完成。", String.valueOf(worker.get("error"))).get("result");
                if (worker.get("dialogue_state") != null) result.set("dialogue_state", objectMapper.valueToTree(worker.get("dialogue_state")));
                result.put("task_id", taskId); result.put("task_status", workerStatus);
                ChatSession session = sessionService.requireOwned(userId, turn.sessionUid());
                if (!result.has("dialogue_state")) clearFailureFacts(result, session);
                turnService.finish(userId, taskId, session, result.path("answer").path("content").asText(), buildMetadata(result), result, workerStatus);
            }
        } catch (Exception unavailable) {
            // 作品说明：传输断连不能作为后台任务已经结束的证明。
        }
        return turnService.owned(userId, taskId);
    }

    public ChatTurnService.Turn cancel(long userId, String taskId) {
        ChatTurnService.Turn turn = turnService.owned(userId, taskId);
        if (turn.terminal()) return turn;
        turnService.requestCancel(userId, taskId);
        try {
            Map<String, Object> worker = agentClient.cancelTask(taskId, turn.sessionUid()).block(Duration.ofSeconds(9));
            String workerStatus = worker == null ? "" : String.valueOf(worker.get("task_status"));
            if (!List.of("cancelled", "completed", "failed").contains(workerStatus)) return turnService.owned(userId, taskId);
            if (!"cancelled".equals(workerStatus)) {
                JsonNode stored=worker.get("result")==null ? null : objectMapper.valueToTree(worker.get("result"));
                if (stored==null || !stored.isObject()) return turnService.owned(userId,taskId);
                requireResultIdentity(stored,taskId);
                ChatSession session=sessionService.requireOwned(userId,turn.sessionUid());
                String terminal="failed".equals(workerStatus) || isFailedResult(stored) ? "failed" : "completed";
                boolean won=turnService.finish(userId,taskId,session,stored.path("answer").path("content").asText(),buildMetadata(stored),stored,terminal);
                if (won) {
                    agentClient.acknowledgeSaved(taskId,turn.sessionUid());
                    LiveTurn live=liveTurns.get(taskId);
                    if (live!=null) {
                        live.doneReceived().set(true);
                        ObjectNode completedRoot=objectMapper.createObjectNode();completedRoot.set("result",stored);
                        completedRoot.put("session_uid",turn.sessionUid());completedRoot.put("persistence_status","saved");
                        sendSafely(live.emitter(),live.clientGone(),"done",completedRoot.toString());
                        releaseTurnLock(live.lockKey(),live.lockToken());
                    }
                }
                return turnService.owned(userId,taskId);
            }
            ObjectNode root = failureRoot(turn.question(), "本轮已取消。", "user_cancelled");
            ObjectNode result = (ObjectNode) root.get("result");
            result.put("task_id", taskId); result.put("task_status", "cancelled");
            result.putObject("outcome").put("status", "cancelled").putArray("reason_codes").add("user_cancelled");
            if (worker.get("dialogue_state") != null) result.set("dialogue_state", objectMapper.valueToTree(worker.get("dialogue_state")));
            ChatSession session = sessionService.requireOwned(userId, turn.sessionUid());
            boolean won = turnService.finish(userId, taskId, session, "本轮已取消。", buildMetadata(result), result, "cancelled");
            if (won) {
                agentClient.acknowledgeSaved(taskId, turn.sessionUid());
                LiveTurn live = liveTurns.get(taskId);
                if (live != null) {
                    live.doneReceived().set(true);
                    root.put("session_uid", turn.sessionUid()); root.put("persistence_status", "saved");
                    sendSafely(live.emitter(), live.clientGone(), "done", root.toString());
                    releaseTurnLock(live.lockKey(), live.lockToken());
                }
            }
        } catch (Exception unavailable) {
            log.warn("[chat] cancellation awaiting worker acknowledgement task={}", taskId);
        }
        return turnService.owned(userId, taskId);
    }

    @Scheduled(fixedDelay = 15_000)
    public void recoverDetachedTasks() {
        try {
            for (ChatTurnService.Turn turn : turnService.recoverable()) {
                if (!liveTurns.containsKey(turn.taskId()) || System.currentTimeMillis() > turn.deadline()) {
                    if (recovering.add(turn.taskId())) recoveryWorkers.submit(() -> {
                        try { status(turn.userId(), turn.taskId()); }
                        finally { recovering.remove(turn.taskId()); }
                    });
                }
            }
        } catch (Exception unavailable) {
            log.warn("[chat] task recovery deferred until dependencies are available");
        }
    }

    @PreDestroy
    public void closeRecoveryWorkers() { recoveryWorkers.shutdownNow(); }

    private void requireResultIdentity(JsonNode result, String taskId) {
        if (result.path("version").asInt()!=3 || !taskId.equals(result.path("task_id").asText()))
            throw new IllegalArgumentException("Result contract or task identity mismatch");
    }

    private void handleEvent(ServerSentEvent<String> event, ChatSession session, StreamRequest request,
                              long userId, String traceId, long startedAt, String taskId,
                              SseEmitter emitter, AtomicBoolean clientGone, AtomicBoolean metricRecorded,
                              AtomicBoolean doneReceived) {
        String name = event.event() == null ? "message" : event.event();
        String data = event.data() == null ? "{}" : event.data();

        if (doneReceived.get()) return;
        if (!"done".equals(name)) {
            if ("plan".equals(name)) {
                try {
                    JsonNode progress = objectMapper.readTree(data);
                    if (taskId.equals(progress.path("task_id").asText())) {
                        turnService.observeWorkerState(taskId, progress.path("task_status").asText());
                    }
                } catch (Exception ignored) { /* 作品说明：进度事件不能宣称任务终态。 */ }
            }
            sendSafely(emitter, clientGone, name, data);
            return;
        }
        if (!doneReceived.compareAndSet(false, true)) return;

        // 作品说明：done 事件：解析结果 → 事务落库 → 审计/标题异步任务 → 转发（带 session_uid）
        String status = "ok";
        String forwarded;
        ObjectNode enriched;
        JsonNode result;
        try {
            JsonNode root = objectMapper.readTree(data);
            result = root.path("result");
            JsonNode content = result.path("answer").path("content");
            JsonNode charts=result.path("chart_data_list");
            boolean chartDeliverable=charts.isArray() && !charts.isEmpty() && result.path("version").asInt()==3;
            if (!root.isObject() || !result.isObject() || !content.isTextual() || content.asText().isBlank() && !chartDeliverable) {
                throw new IllegalArgumentException("Invalid done payload");
            }
            requireResultIdentity(result,taskId);
            enriched = (ObjectNode) root;
        } catch (Exception invalidResponse) {
            log.warn("[chat] invalid upstream done session={} traceId={}", session.getSessionUid(), traceId, invalidResponse);
            enriched = failureRoot(request.question(),
                    "服务返回的回答格式无效，本轮未取得结果，请重试。", "invalid_upstream_response");
            result = enriched.path("result");
            ((ObjectNode)result).put("task_id",taskId).put("task_status","failed");
        }
        try {
            String content = result.path("answer").path("content").asText("");
            String metadataJson = buildMetadata(result);
            try {
                String terminal = "cancelled".equals(result.path("task_status").asText()) ? "cancelled"
                        : isFailedResult(result) || "failed".equals(result.path("task_status").asText()) ? "failed" : "completed";
                boolean firstTurn = session.getMessageCount() == null || session.getMessageCount() == 0;
                boolean saved = turnService.finish(userId, taskId, session, content, metadataJson, result, terminal);
                if (!saved) return; // 作品说明：已取消任务及重复、迟到的终态事件不再提交结果。
                agentClient.acknowledgeSaved(taskId, session.getSessionUid());
                enriched.put("persistence_status", "saved");
                if (firstTurn && result.path("error").asText("").isBlank() && !isFailedResult(result)) {
                    try {
                        eventPublisher.publishTitleTask(new TitleGenMessage(
                                session.getId(), session.getSessionUid(), request.question(),
                                content.length() > 200 ? content.substring(0, 200) : content));
                    } catch (Exception titleError) {
                        log.warn("[chat] title task failed session={}", session.getSessionUid(), titleError);
                    }
                }
            } catch (Exception persistenceError) {
                log.error("[chat] persist failed session={}", session.getSessionUid(), persistenceError);
                enriched.put("persistence_status", "failed");
                enriched.put("error", "回答未能保存到历史记录，请保留当前页面并稍后重试。");
                status = "error";
                turnService.saveFailed(userId, taskId, result);
            }
            if (isFailedResult(result)) {
                status = "error";
            }
            enriched.put("session_uid", session.getSessionUid());
            forwarded = objectMapper.writeValueAsString(enriched);
        } catch (Exception e) {
            log.error("[chat] done 落库失败 session={} traceId={}", session.getSessionUid(), traceId, e);
            status = "error";
            ObjectNode error = objectMapper.createObjectNode();
            error.put("message", "服务返回的回答格式无效，本轮未保存，请重试。");
            error.put("terminal", true);
            sendSafely(emitter, clientGone, "error", error.toString());
            recordChatMetric(metricRecorded, status, startedAt);
            return;
        }
        try {
            publishChatLog(userId, session.getSessionUid(), traceId, request.question(), status, startedAt);
        } catch (Exception auditError) {
            // 作品说明：审计存储不可用时，仍返回回答与真实保存状态。
            log.warn("[chat] audit publish failed session={}", session.getSessionUid(), auditError);
        }
        recordChatMetric(metricRecorded, status, startedAt);
        sendSafely(emitter, clientGone, "done", forwarded);
    }

    /** 作品说明：助手消息元数据：从 done 结果中拷贝展示所需字段（answer.content 落在消息正文，不重复存） */
    String buildMetadata(JsonNode result) {
        ObjectNode metadata = objectMapper.createObjectNode();
        metadata.set("sql", result.path("sql"));
        if (result.path("query_trace").isArray()) metadata.set("query_trace", result.path("query_trace"));
        metadata.set("chart_format", result.path("chart_format"));
        metadata.set("chart_data", result.path("chart_data"));
        metadata.set("chart_data_list", result.path("chart_data_list"));
        metadata.set("images", result.path("answer").path("image"));
        metadata.set("references", result.path("answer").path("references"));
        metadata.set("validation", result.path("validation"));
        for (String field : new String[]{"version", "task_id", "task_status", "deadline", "data_version", "dataset_profile", "verification_v3", "verification", "evidence", "facts", "outcome", "query_plan", "derived_facts", "comparisons", "execution_plan", "dialogue_state", "response_kind", "chart_eligibility", "request_contract", "answer_assessment", "task_results", "catalog_result", "diagnostics"}) {
            JsonNode value = result.path(field);
            if (value.isMissingNode() || value.isNull()) value = result.path("validation").path(field);
            if (!value.isMissingNode() && !value.isNull()) metadata.set(field, value);
        }
        metadata.set("error", result.path("error"));
        metadata.put("persistence_status", "saved");
        metadata.set("execution_plan", result.path("execution_plan"));
        metadata.set("needs_clarification", result.path("needs_clarification"));
        metadata.set("clarify_options", result.path("clarify_options"));
        metadata.set("context", result.path("context"));
        return metadata.toString();
    }

    private void releaseTurnLock(String key, String token) {
        try {
            redisTemplate.execute(RELEASE_TURN, List.of(key), token);
        } catch (RuntimeException unavailable) {
            log.warn("[chat] turn lock release unavailable; TTL will release it");
        }
    }

    private void completeFailure(String message, ChatSession session, StreamRequest request,
                                 long userId, String traceId, long startedAt, String taskId, SseEmitter emitter,
                                 AtomicBoolean clientGone, AtomicBoolean metricRecorded, AtomicBoolean doneReceived) {
        if (doneReceived.get()) return;
        ChatTurnService.Turn turn = status(userId, taskId);
        if (turn.terminal() && turn.result() != null) {
            ObjectNode root = objectMapper.createObjectNode(); root.set("result", turn.result());
            root.put("persistence_status", turn.saved() ? "saved" : "failed"); root.put("session_uid", turn.sessionUid());
            doneReceived.set(true);
            sendSafely(emitter, clientGone, "done", root.toString());
            recordChatMetric(metricRecorded, "error", startedAt);
            try { publishChatLog(userId, turn.sessionUid(), traceId, request.question(), "error", startedAt); }
            catch (Exception auditFailure) { log.warn("[chat] recovery audit deferred task={}", taskId); }
        } else {
            sendSafely(emitter, clientGone, "error", "{\"message\":\"连接中断，后台状态正在恢复。\",\"code\":\"task_recovery_pending\"}");
        }
    }

    private void stopWorker(String taskId, String sessionUid) {
        agentClient.cancelTask(taskId, sessionUid).subscribe(ignored -> {}, ignored -> {});
    }

    private void clearFailureFacts(ObjectNode result, ChatSession session) {
        List<Map<String, Object>> history = sessionService.buildHistory(session.getId());
        for (int i = history.size() - 1; i >= 0; i--) {
            Object metadata = history.get(i).get("metadata");
            JsonNode state = objectMapper.valueToTree(metadata).path("dialogue_state");
            if (state.isObject() && state.path("version").asInt() == 3) {
                ObjectNode cleared = state.deepCopy(); cleared.putArray("recent_facts"); cleared.putArray("recent_computed"); cleared.putNull("last_execution");
                ObjectNode interrupted = cleared.putObject("interrupted_request");
                interrupted.put("turn_id", result.path("task_id").asText("unknown"));
                interrupted.put("question", result.path("question").asText(""));
                interrupted.put("status", "cancelled".equals(result.path("task_status").asText()) ? "cancelled" : "failed");
                interrupted.putArray("confirmed_fields");
                result.set("dialogue_state", cleared); break;
            }
        }
    }

    private ObjectNode failureRoot(String question, String message, String reasonCode) {
        ObjectNode result = objectMapper.createObjectNode();
        result.put("version", 3);
        result.put("question", question);
        result.put("error", message);
        result.putObject("answer").put("content", message).putArray("references");
        result.putObject("validation").put("status", "fail");
        ObjectNode verification = result.putObject("verification");
        ObjectNode outcome = result.putObject("outcome");
        outcome.put("status", "query_failed");
        outcome.putArray("reason_codes").add(reasonCode);
        verification.put("status", "fail");
        verification.put("scope", "本轮执行未完成，回答未核验");
        verification.putArray("checks");
        verification.putArray("unmatched_numbers");
        result.putArray("evidence");
        result.putArray("facts");
        ObjectNode root = objectMapper.createObjectNode();
        root.set("result", result);
        return root;
    }

    private boolean isFailedResult(JsonNode result) {
        return "query_failed".equals(result.path("outcome").path("status").asText(""))
                || "fail".equals(result.path("validation").path("status").asText(""))
                || "fail".equals(result.path("verification").path("status").asText(""));
    }

    /** 作品说明：审计经 MQ 异步落库（broker 不可用时发布器内部降级直写） */
    private void publishChatLog(long userId, String sessionUid, String traceId, String question,
                                String status, long startedAt) {
        eventPublisher.publishChatLog(new ChatLogMessage(
                userId,
                sessionUid,
                traceId == null ? "" : traceId,
                question.length() > 1000 ? question.substring(0, 1000) : question,
                status,
                (int) Math.min(System.currentTimeMillis() - startedAt, Integer.MAX_VALUE)));
    }

    private void recordChatMetric(AtomicBoolean metricRecorded, String status, long startedAt) {
        if (!metricRecorded.compareAndSet(false, true)) {
            return;
        }
        observabilityService.recordChatTurn(status, System.currentTimeMillis() - startedAt);
    }

    private void releaseActiveSse(AtomicBoolean activeReleased) {
        if (activeReleased.compareAndSet(false, true)) {
            observabilityService.decrementActiveSse();
        }
    }

    private void sendSafely(SseEmitter emitter, AtomicBoolean clientGone, String name, String data) {
        if (clientGone.get()) {
            return;
        }
        try {
            emitter.send(SseEmitter.event().name(name).data(data));
        } catch (IOException | IllegalStateException e) {
            clientGone.set(true);
        }
    }
}
