package com.finsight.chat;

import com.finsight.common.trace.TraceIdFilter;
import com.finsight.etl.EtlExecutionUncertainException;
import io.github.resilience4j.circuitbreaker.annotation.CircuitBreaker;
import io.github.resilience4j.retry.annotation.Retry;
import lombok.RequiredArgsConstructor;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.MediaType;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.stereotype.Component;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.reactive.function.client.WebClientRequestException;
import reactor.core.publisher.Flux;

import java.util.Map;

/** Python AI 微服务客户端：SSE 流式对话透传 + 内部接口调用。 */
@Component
@RequiredArgsConstructor
public class AgentClient {

    private static final ParameterizedTypeReference<ServerSentEvent<String>> SSE_TYPE =
            new ParameterizedTypeReference<>() {
            };

    private final WebClient agentWebClient;
    private final WebClient etlWebClient;

    @Value("${finsight.etl.request-timeout:2h}")
    private java.time.Duration etlRequestTimeout = java.time.Duration.ofHours(2);

    /** Company views pin the same release and source policy as financial chat. */
    public com.finsight.market.dto.MarketDtos.MarketSnapshot marketSnapshot() {
        var result = agentWebClient.get().uri("/internal/market/snapshot").retrieve()
                .bodyToMono(com.finsight.market.dto.MarketDtos.MarketSnapshot.class)
                .block(java.time.Duration.ofSeconds(10));
        if (result == null) throw new IllegalStateException("当前财报目录暂不可用");
        return result;
    }

    public Map<String, Object> financialCandidate(String candidateId) {
        return requireFinancialResponse(etlWebClient.get()
                .uri("/internal/financial-submissions/{id}", candidateId).retrieve()
                .bodyToMono(new ParameterizedTypeReference<Map<String, Object>>() {}).block(etlRequestTimeout));
    }

    public Map<String, Object> reviewFinancialCandidate(String candidateId, Map<String, Object> payload) {
        return requireFinancialResponse(etlWebClient.post()
                .uri("/internal/financial-submissions/{id}/review", candidateId)
                .contentType(MediaType.APPLICATION_JSON).bodyValue(payload).retrieve()
                .bodyToMono(new ParameterizedTypeReference<Map<String, Object>>() {}).block(etlRequestTimeout));
    }

    public Map<String, Object> publishFinancialCandidates(java.util.List<String> candidateIds, long operatorId) {
        // A retry is explicit: publication is never hidden behind a generic HTTP retry annotation.
        return requireFinancialResponse(etlWebClient.post().uri("/internal/financial-submissions/publish")
                .contentType(MediaType.APPLICATION_JSON)
                .bodyValue(Map.of("candidate_ids", candidateIds, "operator_id", operatorId)).retrieve()
                .bodyToMono(new ParameterizedTypeReference<Map<String, Object>>() {}).block(etlRequestTimeout));
    }

    private static Map<String, Object> requireFinancialResponse(Map<String, Object> response) {
        if (response == null) throw new IllegalStateException("Financial service returned no receipt");
        return response;
    }

    /**
     * 调用 Python /internal/chat/stream，原样返回 SSE 事件流
     * （plan / tool_call / tool_result / answer_delta / chart / clarify / error / done）。
     */
    @CircuitBreaker(name = "agent")
    public Flux<ServerSentEvent<String>> streamChat(Map<String, Object> payload, String traceId) {
        return agentWebClient.post()
                .uri("/internal/chat/stream")
                .contentType(MediaType.APPLICATION_JSON)
                .accept(MediaType.TEXT_EVENT_STREAM)
                .header(TraceIdFilter.HEADER, traceId)
                .bodyValue(payload)
                .retrieve()
                .onStatus(status -> status.value() == 409, response -> response
                        .bodyToMono(com.fasterxml.jackson.databind.JsonNode.class)
                        .map(body -> "model_queue_full".equals(body.path("detail").asText())
                                ? (Throwable) new AgentAdmissionException()
                                : new IllegalStateException("Agent admission rejected")))
                .bodyToFlux(SSE_TYPE);
    }

    public reactor.core.publisher.Mono<Map<String, Object>> taskStatus(String taskId, String sessionUid) {
        return agentWebClient.get().uri(builder -> builder.path("/internal/tasks/{id}")
                .queryParam("session_uid", sessionUid).build(taskId)).retrieve()
                .bodyToMono(new ParameterizedTypeReference<Map<String, Object>>() {}).timeout(java.time.Duration.ofSeconds(5));
    }

    public reactor.core.publisher.Mono<Map<String, Object>> cancelTask(String taskId, String sessionUid) {
        return agentWebClient.post().uri("/internal/tasks/{id}/cancel", taskId)
                .bodyValue(Map.of("session_uid", sessionUid)).retrieve()
                .bodyToMono(new ParameterizedTypeReference<Map<String, Object>>() {}).timeout(java.time.Duration.ofSeconds(8));
    }

    public void acknowledgeSaved(String taskId, String sessionUid) {
        agentWebClient.post().uri("/internal/tasks/{id}/saved", taskId)
                .bodyValue(Map.of("session_uid", sessionUid)).retrieve().toBodilessEntity()
                .subscribe(ignored -> {}, ignored -> {});
    }

    /** Python 侧健康检查原始 JSON（service/database/knowledge_base/llm） */
    @Retry(name = "agent")
    @CircuitBreaker(name = "agent")
    public String fetchHealthRaw() {
        return agentWebClient.get()
                .uri("/internal/health")
                .retrieve()
                .bodyToMono(String.class)
                .block(java.time.Duration.ofSeconds(5));
    }

    /** 删除会话名下的图表文件（fire-and-forget，失败不影响主流程） */
    public void deleteSessionCharts(String sessionUid) {
        agentWebClient.delete()
                .uri("/internal/charts/{uid}", sessionUid)
                .retrieve()
                .toBodilessEntity()
                .subscribe(ignored -> {
                }, error -> {
                });
    }

    /** 单文件 ETL 管线（同步长调用，由 MQ 消费者串行触发），返回 Python 管线步骤报告 */
    public Map<String, Object> runEtl(String filePath, String fileType, boolean ingestRag) {
        try {
            Map<String, Object> response = etlWebClient.post()
                .uri("/internal/etl/run")
                .contentType(MediaType.APPLICATION_JSON)
                .bodyValue(Map.of(
                        "file_path", filePath,
                        "file_type", fileType,
                        "ingest_rag", ingestRag))
                .retrieve()
                .bodyToMono(new ParameterizedTypeReference<Map<String, Object>>() {
                })
                .block(etlRequestTimeout);
        if (response == null) {
            throw new EtlExecutionUncertainException(null);
        }
        return response;
        } catch (WebClientRequestException e) {
            throw new EtlExecutionUncertainException(e);
        } catch (IllegalStateException e) {
            if (e.getCause() instanceof java.util.concurrent.TimeoutException) {
                throw new EtlExecutionUncertainException(e);
            }
            throw e;
        }
    }

    /** AI 诊股报告生成（LLM），失败抛异常由 MQ 重试 */
    @Retry(name = "agent")
    @CircuitBreaker(name = "agent")
    public String generateAdvisorReport(Map<String, Object> payload) {
        Map<String, String> response = agentWebClient.post()
                .uri("/internal/advisor/report")
                .contentType(MediaType.APPLICATION_JSON)
                .bodyValue(payload)
                .retrieve()
                .bodyToMono(new ParameterizedTypeReference<Map<String, String>>() {
                })
                .block(java.time.Duration.ofMinutes(3));
        String report = response == null ? null : response.get("report_md");
        if (report == null || report.isBlank()) {
            throw new IllegalStateException("诊股报告生成为空");
        }
        return report;
    }

    /** LLM 概括会话标题；失败返回 null（调用侧保留兜底标题） */
    @Retry(name = "agent")
    @CircuitBreaker(name = "agent")
    public String generateTitle(String question, String answerSnippet) {
        try {
            Map<String, String> response = agentWebClient.post()
                    .uri("/internal/title")
                    .contentType(MediaType.APPLICATION_JSON)
                    .bodyValue(Map.of(
                            "question", question == null ? "" : question,
                            "answer", answerSnippet == null ? "" : answerSnippet))
                    .retrieve()
                    .bodyToMono(new ParameterizedTypeReference<Map<String, String>>() {
                    })
                    .block(java.time.Duration.ofSeconds(20));
            return response == null ? null : response.get("title");
        } catch (Exception e) {
            throw new IllegalStateException("标题生成调用失败: " + e.getMessage(), e);
        }
    }
}
