package com.finsight.chat;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.finsight.chat.dto.ChatDtos.StreamRequest;
import com.finsight.chat.entity.ChatSession;
import com.finsight.chat.mq.ChatEventPublisher;
import com.finsight.common.metrics.ObservabilityService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ValueOperations;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.servlet.mvc.method.annotation.ResponseBodyEmitter;
import reactor.core.publisher.Flux;
import java.time.Duration;
import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class ChatStreamServiceTest {
    private final AgentClient agent = mock(AgentClient.class);
    private final ChatSessionService sessions = mock(ChatSessionService.class);
    private final ChatEventPublisher events = mock(ChatEventPublisher.class);
    private final StringRedisTemplate redis = mock(StringRedisTemplate.class);
    private final ObjectMapper json = new ObjectMapper();
    private final ObservabilityService metrics = mock(ObservabilityService.class);
    private final ChatTurnService turns = mock(ChatTurnService.class);
    private final ChatStreamService service = new ChatStreamService(agent, sessions, events, redis, json, metrics, turns);
    private final ChatSession session = new ChatSession();
    private final java.util.concurrent.atomic.AtomicReference<ChatTurnService.Turn> savedTurn = new java.util.concurrent.atomic.AtomicReference<>();

    @BeforeEach @SuppressWarnings("unchecked") void setup() {
        ValueOperations<String, String> values = mock(ValueOperations.class);
        when(redis.opsForValue()).thenReturn(values);
        when(values.setIfAbsent(anyString(), anyString(), any(Duration.class))).thenReturn(true);
        session.setId(1L); session.setSessionUid("s1"); session.setMessageCount(0);
        when(sessions.create(7)).thenReturn(session);
        when(sessions.buildHistory(1)).thenReturn(List.of());
        when(turns.create(eq(7L), eq(session), anyString(), anyString())).thenAnswer(call ->
            new ChatTurnService.Turn("task1",7,1,"s1",call.getArgument(2),call.getArgument(3),"queued",System.currentTimeMillis()+270000,null,false,null));
        when(turns.claimDispatch("task1")).thenReturn(true);
        savedTurn.set(new ChatTurnService.Turn("task1",7,1,"s1","c1","q","running",System.currentTimeMillis()+270000,null,false,null));
        when(turns.owned(7,"task1")).thenAnswer(call -> savedTurn.get());
        when(sessions.requireOwned(7,"s1")).thenReturn(session);
        when(agent.taskStatus(anyString(), anyString())).thenReturn(reactor.core.publisher.Mono.empty());
        when(turns.finish(eq(7L),eq("task1"),eq(session),anyString(),anyString(),any(),anyString())).thenAnswer(call -> {
            sessions.persistTurn(session,"q",call.getArgument(3),call.getArgument(4));
            savedTurn.set(new ChatTurnService.Turn("task1",7,1,"s1","c1","q",call.getArgument(6),System.currentTimeMillis()+270000,call.getArgument(5),true,null));
            return true;
        });
    }
    @Test void metadataPreservesCanonicalEvidenceAndLegacyCompatibility() throws Exception {
        var canonical = json.readTree("{\"verification\":{\"status\":\"warn\",\"scope\":\"facts only\"},\"evidence\":[{\"id\":\"q1\"},{\"id\":\"q2\"}],\"facts\":[{\"fact_id\":\"f1\"}],\"answer\":{\"references\":[{\"asset_id\":\"0123456789abcdef\",\"page_start\":8}]}}");
        var saved = json.readTree(service.buildMetadata(canonical));
        assertEquals(canonical.get("verification"), saved.get("verification"));
        assertEquals(2, saved.get("evidence").size());
        assertEquals(8, saved.path("references").get(0).path("page_start").asInt());
        assertEquals(canonical.get("facts"), saved.get("facts"));
        var legacy = json.readTree("{\"validation\":{\"verification\":{\"status\":\"pass\"},\"evidence\":[{\"id\":\"q1\"}]}}");
        assertEquals("pass", json.readTree(service.buildMetadata(legacy)).path("verification").path("status").asText());
    }
    @Test void upstreamFailureProducesPersistedFailedTerminalAnswer() throws Exception {
        when(agent.taskStatus(anyString(), anyString())).thenReturn(reactor.core.publisher.Mono.just(java.util.Map.of("task_status","failed","error","model_connection_failed")));
        when(agent.streamChat(anyMap(), anyString())).thenReturn(Flux.error(new IllegalStateException("offline")));
        var context = service.stream(7, new StreamRequest("q", null, null), "trace");
        ArgumentCaptor<String> metadata = ArgumentCaptor.forClass(String.class);
        verify(sessions).persistTurn(eq(session), eq("q"), contains("本轮未完成"), metadata.capture());
        assertEquals("fail", json.readTree(metadata.getValue()).path("verification").path("status").asText());
        assertEquals("query_failed", json.readTree(metadata.getValue()).path("outcome").path("status").asText());
        assertTrue(transcript(context.emitter()).contains("\"persistence_status\":\"saved\""));
        verify(metrics, times(1)).decrementActiveSse();
    }
    @Test void queueRefusalIsPersistedImmediatelyWithoutWaitingForAnAbsentWorker() throws Exception {
        when(agent.streamChat(anyMap(), anyString())).thenReturn(Flux.error(new AgentAdmissionException()));
        var context = service.stream(7, new StreamRequest("q", null, null), "trace");
        ArgumentCaptor<String> metadata = ArgumentCaptor.forClass(String.class);
        verify(sessions).persistTurn(eq(session), eq("q"), contains("队列已满"), metadata.capture());
        assertEquals("model_queue_full", json.readTree(metadata.getValue()).path("outcome").path("reason_codes").get(0).asText());
        assertTrue(savedTurn.get().terminal());
        assertEquals("failed", savedTurn.get().status());
        assertTrue(transcript(context.emitter()).contains("\"persistence_status\":\"saved\""));
        verify(agent, never()).taskStatus(anyString(), anyString());
        verify(events, never()).publishTitleTask(any());
    }
    @Test void dispatchDoesNotClaimExecutionAndOnlyBoundProgressCanStartIt() {
        when(agent.streamChat(anyMap(), anyString())).thenReturn(Flux.just(
            ServerSentEvent.<String>builder("{\"task_id\":\"task1\",\"task_status\":\"queued\"}").event("plan").build(),
            ServerSentEvent.<String>builder("{\"task_id\":\"foreign\",\"task_status\":\"running\"}").event("plan").build(),
            ServerSentEvent.<String>builder("{\"task_id\":\"task1\",\"task_status\":\"running\"}").event("plan").build()));
        service.stream(7, new StreamRequest("q", null, null), "trace");
        verify(turns).claimDispatch("task1");
        verify(turns).observeWorkerState("task1", "queued");
        verify(turns, times(1)).observeWorkerState("task1", "running");
        verify(turns, never()).observeWorkerState(eq("foreign"), anyString());
    }
    @Test void resultStateAndPlanSurvivePersistenceWithoutInventingLegacyState() throws Exception {
        var result = json.readTree("""
            {"outcome":{"status":"partial","reason_codes":["metric_value_missing"]},
             "query_plan":{"codes":["002082"],"pairs":[[2024,"FY"]]},
             "derived_facts":[{"input_fact_ids":["f1","f2"],"formula":"current-base"}],
             "response_kind":"conversation",
             "dialogue_state":{"version":1,"last_intent":"help","active_request":{"codes":["002082"]}},
             "chart_eligibility":[{"reason":"insufficient_points"}]}
            """);
        var saved = json.readTree(service.buildMetadata(result));
        for (String field : List.of("outcome", "query_plan", "derived_facts", "response_kind", "dialogue_state", "chart_eligibility")) {
            assertEquals(result.get(field), saved.get(field));
        }
        assertFalse(json.readTree(service.buildMetadata(json.readTree("{}"))).has("outcome"));
        assertFalse(json.readTree(service.buildMetadata(json.readTree("{\"outcome\":null}"))).has("outcome"));
    }
    @Test void malformedDoneBecomesPersistedFailureInsteadOfUnrecordedError() throws Exception {
        when(agent.streamChat(anyMap(), anyString())).thenReturn(Flux.just(
                ServerSentEvent.<String>builder("{\"result\":{\"answer\":{\"content\":\"\"}}}").event("done").build()));
        when(sessions.persistTurn(any(), anyString(), anyString(), anyString())).thenReturn(true);
        var context = service.stream(7, new StreamRequest("q", null, null), "trace");
        ArgumentCaptor<String> metadata = ArgumentCaptor.forClass(String.class);
        verify(sessions).persistTurn(eq(session), eq("q"), contains("格式无效"), metadata.capture());
        var saved = json.readTree(metadata.getValue());
        assertEquals("query_failed", saved.path("outcome").path("status").asText());
        assertEquals("invalid_upstream_response", saved.path("outcome").path("reason_codes").get(0).asText());
        assertTrue(transcript(context.emitter()).contains("\"persistence_status\":\"saved\""));
        verify(metrics).recordChatTurn(eq("error"), anyLong());
        verify(events, never()).publishTitleTask(any());
    }
    @Test void queryFailedOutcomeCountsAsFailureEvenWhenLegacyValidationIsNotFail() throws Exception {
        when(agent.streamChat(anyMap(), anyString())).thenReturn(Flux.just(
                ServerSentEvent.<String>builder("""
                    {"result":{"version":3,"task_id":"task1","answer":{"content":"本轮未取得结果"},
                     "outcome":{"status":"query_failed","reason_codes":["sql_execution_failed"]},
                     "validation":{"status":"warn"},"verification":{"status":"warn"}}}
                    """).event("done").build()));
        when(sessions.persistTurn(any(), anyString(), anyString(), anyString())).thenReturn(true);
        service.stream(7, new StreamRequest("q", null, null), "trace");
        verify(metrics).recordChatTurn(eq("error"), anyLong());
        verify(events).publishChatLog(argThat(message -> "error".equals(message.status())));
        verify(events, never()).publishTitleTask(any());
    }
    @Test void synchronousTransportFailureRetainsDurableTaskForRecovery() throws Exception {
        when(agent.streamChat(anyMap(), anyString())).thenThrow(new IllegalStateException("agent unavailable"));
        var context = service.stream(7, new StreamRequest("q", null, null), "trace");
        verify(sessions, never()).persistTurn(any(), anyString(), anyString(), anyString());
        assertTrue(transcript(context.emitter()).contains("task_recovery_pending"));
        assertFalse(savedTurn.get().terminal());
    }
    @Test void duplicateDoneAndLateEventsCannotChangePersistedTurn() {
        when(agent.streamChat(anyMap(), anyString())).thenReturn(Flux.just(
                done(), done(), ServerSentEvent.<String>builder("{\"message\":\"late\"}").event("error").build()));
        var context = service.stream(7, new StreamRequest("q", null, null), "trace");
        verify(sessions, times(1)).persistTurn(any(), anyString(), anyString(), anyString());
        verify(metrics, times(1)).recordChatTurn(eq("ok"), anyLong());
        assertFalse(transcript(context.emitter()).contains("late"));
    }
    @Test void emptyUpstreamCannotSilentlyComplete() {
        when(agent.streamChat(anyMap(), anyString())).thenReturn(Flux.empty());
        var context = service.stream(7, new StreamRequest("q", null, null), "trace");
        verify(sessions, never()).persistTurn(any(), anyString(), anyString(), anyString());
        assertTrue(transcript(context.emitter()).contains("task_recovery_pending"));
        assertFalse(savedTurn.get().terminal());
    }
    @Test void persistenceFailureIsVisibleInDoneAndIsNotRetriedAsSuccess() {
        when(agent.streamChat(anyMap(), anyString())).thenReturn(Flux.just(done()));
        when(sessions.persistTurn(any(), anyString(), anyString(), anyString())).thenThrow(new IllegalStateException("database down"));
        var context = service.stream(7, new StreamRequest("q", null, null), "trace");
        String body = transcript(context.emitter());
        assertTrue(body.contains("\"persistence_status\":\"failed\""));
        assertTrue(body.contains("未能保存"));
        verify(sessions, times(1)).persistTurn(any(), anyString(), anyString(), anyString());
        verify(events, never()).publishTitleTask(any());
    }
    @Test void auditFailureCannotSuppressSavedTerminalResponse() {
        when(agent.streamChat(anyMap(), anyString())).thenReturn(Flux.just(done()));
        doThrow(new IllegalStateException("audit unavailable")).when(events).publishChatLog(any());
        var context = service.stream(7, new StreamRequest("q", null, null), "trace");
        assertTrue(transcript(context.emitter()).contains("\"persistence_status\":\"saved\""));
    }
    @Test void foreignTaskResultCannotBeSavedAsTheCurrentTask() throws Exception {
        when(agent.streamChat(anyMap(),anyString())).thenReturn(Flux.just(
            ServerSentEvent.<String>builder("{\"result\":{\"version\":3,\"task_id\":\"other-task\",\"answer\":{\"content\":\"foreign answer\"}}}").event("done").build()));
        service.stream(7,new StreamRequest("q",null,null),"trace");
        ArgumentCaptor<String> content=ArgumentCaptor.forClass(String.class);
        verify(sessions).persistTurn(eq(session),eq("q"),content.capture(),anyString());
        assertFalse(content.getValue().contains("foreign answer"));
        assertEquals("failed",savedTurn.get().status());
        assertEquals("task1",savedTurn.get().result().path("task_id").asText());
    }
    @Test void expiredCancellationWithoutWorkerAcknowledgementIsFailedNotCancelled() {
        savedTurn.set(new ChatTurnService.Turn("task1",7,1,"s1","c1","q","cancelling",System.currentTimeMillis()-1000,null,false,null));
        when(agent.cancelTask("task1","s1")).thenReturn(reactor.core.publisher.Mono.empty());
        service.status(7,"task1");
        assertEquals("failed",savedTurn.get().status());
        assertTrue(savedTurn.get().result().path("answer").path("content").asText().contains("停止尚未取得确认"));
    }
    @Test void chartOnlyResultCanBeSavedWithoutInventingFinancialProse() {
        when(agent.streamChat(anyMap(),anyString())).thenReturn(Flux.just(
            ServerSentEvent.<String>builder("""
                {"result":{"version":3,"task_id":"task1","answer":{"content":""},
                "chart_data_list":[{"option":{"series":[{"type":"bar","data":[1,2]}]}}],
                "verification_v3":{"status":"pass"}}}
                """).event("done").build()));
        service.stream(7,new StreamRequest("q",null,null),"trace");
        verify(sessions).persistTurn(eq(session),eq("q"),eq(""),anyString());
        assertEquals("completed",savedTurn.get().status());
        assertEquals(1,savedTurn.get().result().path("chart_data_list").size());
    }
    @Test void completionWinningWorkerCancellationRaceKeepsCompletedResult() throws Exception {
        var result=json.readTree("{\"version\":3,\"task_id\":\"task1\",\"answer\":{\"content\":\"verified answer\"},\"verification_v3\":{\"status\":\"pass\"}}");
        when(agent.cancelTask("task1","s1")).thenReturn(reactor.core.publisher.Mono.just(java.util.Map.of("task_status","completed","result",result)));
        service.cancel(7,"task1");
        assertEquals("completed",savedTurn.get().status());
        assertEquals("verified answer",savedTurn.get().result().path("answer").path("content").asText());
        assertNotEquals("cancelled",savedTurn.get().result().path("task_status").asText());
    }
    private static ServerSentEvent<String> done() {
        return ServerSentEvent.<String>builder("{\"result\":{\"version\":3,\"task_id\":\"task1\",\"answer\":{\"content\":\"answer\"},\"verification\":{\"status\":\"pass\"}}}").event("done").build();
    }
    @SuppressWarnings("unchecked") private static String transcript(ResponseBodyEmitter emitter) {
        Set<ResponseBodyEmitter.DataWithMediaType> sent = (Set<ResponseBodyEmitter.DataWithMediaType>) ReflectionTestUtils.getField(emitter, "earlySendAttempts");
        return sent.stream().map(item -> String.valueOf(item.getData())).collect(Collectors.joining());
    }
}
