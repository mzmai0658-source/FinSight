package com.finsight.chat;

import com.finsight.chat.dto.ChatDtos.SessionDetail;
import com.finsight.chat.dto.ChatDtos.SessionSummary;
import com.finsight.chat.dto.ChatDtos.StreamRequest;
import com.finsight.chat.entity.ChatSession;
import com.finsight.common.api.ApiResponse;
import com.finsight.common.ratelimit.DailyQuota;
import com.finsight.common.ratelimit.RateLimit;
import com.finsight.common.security.SecurityUtils;
import com.finsight.common.trace.TraceIdFilter;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.slf4j.MDC;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.util.List;
import java.util.Map;

@Tag(name = "AI 对话", description = "多会话管理与 SSE 流式财报分析")
@RestController
@RequestMapping("/api/chat")
@RequiredArgsConstructor
public class ChatController {

    private final ChatSessionService sessionService;
    private final ChatStreamService streamService;
    private final ChatTurnService turnService;

    @Operation(summary = "创建会话")
    @PostMapping("/sessions")
    public ApiResponse<Map<String, String>> createSession() {
        ChatSession session = sessionService.create(SecurityUtils.currentUserId());
        return ApiResponse.ok(Map.of("sessionUid", session.getSessionUid(), "title", session.getTitle()));
    }

    @Operation(summary = "会话列表（按最近活跃排序）")
    @GetMapping("/sessions")
    public ApiResponse<List<SessionSummary>> listSessions() {
        return ApiResponse.ok(sessionService.list(SecurityUtils.currentUserId()));
    }

    @Operation(summary = "会话详情（含消息）")
    @GetMapping("/sessions/{sessionUid}")
    public ApiResponse<SessionDetail> sessionDetail(@PathVariable String sessionUid) {
        return ApiResponse.ok(sessionService.detail(SecurityUtils.currentUserId(), sessionUid));
    }

    @Operation(summary = "删除会话")
    @DeleteMapping("/sessions/{sessionUid}")
    public ApiResponse<Void> deleteSession(@PathVariable String sessionUid) {
        sessionService.delete(SecurityUtils.currentUserId(), sessionUid);
        return ApiResponse.ok();
    }

    @Operation(summary = "SSE 流式问答（sessionUid 为空时自动建会话，事件流首条为 session 事件）")
    @RateLimit(name = "chat", windowSeconds = 60, limit = 6, message = "提问太快了，请稍等几秒再试")
    @DailyQuota(name = "chat", limit = 100)
    @PostMapping(value = "/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter stream(@Valid @RequestBody StreamRequest request) {
        long userId = SecurityUtils.currentUserId();
        String traceId = MDC.get(TraceIdFilter.MDC_KEY);
        return streamService.stream(userId, request, traceId).emitter();
    }

    @GetMapping("/tasks/{taskId}")
    public ApiResponse<Map<String, Object>> taskStatus(@PathVariable String taskId) {
        return ApiResponse.ok(taskView(streamService.status(SecurityUtils.currentUserId(), taskId)));
    }

    @GetMapping("/tasks/recover")
    public ApiResponse<Map<String, Object>> recover(@RequestParam String clientRequestId) {
        ChatTurnService.Turn turn = turnService.byClient(SecurityUtils.currentUserId(), clientRequestId);
        return ApiResponse.ok(turn == null ? null : taskView(streamService.status(SecurityUtils.currentUserId(), turn.taskId())));
    }

    @GetMapping("/sessions/{sessionUid}/tasks")
    public ApiResponse<List<Map<String, Object>>> activeTasks(@PathVariable String sessionUid) {
        return ApiResponse.ok(turnService.active(SecurityUtils.currentUserId(), sessionUid).stream().map(this::taskView).toList());
    }

    @PostMapping("/tasks/{taskId}/cancel")
    public ApiResponse<Map<String, Object>> cancel(@PathVariable String taskId) {
        return ApiResponse.ok(taskView(streamService.cancel(SecurityUtils.currentUserId(), taskId)));
    }

    private Map<String, Object> taskView(ChatTurnService.Turn turn) {
        Map<String, Object> view = new java.util.LinkedHashMap<>();
        view.put("version", 3); view.put("task_id", turn.taskId());
        view.put("client_request_id", turn.clientRequestId()); view.put("session_uid", turn.sessionUid());
        view.put("status", turn.status()); view.put("deadline", turn.deadline()); view.put("saved", turn.saved());
        view.put("question", turn.question());
        view.put("result", turn.result()); view.put("error", turn.error());
        return view;
    }
}
