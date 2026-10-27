package com.finsight.chat;

import org.junit.jupiter.api.Test;
import org.springframework.http.*;
import org.springframework.http.codec.HttpMessageWriter;
import org.springframework.http.server.reactive.ServerHttpRequest;
import org.springframework.mock.http.client.reactive.MockClientHttpRequest;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.reactive.function.BodyInserter;
import org.springframework.web.reactive.function.client.*;
import reactor.core.publisher.Mono;

import java.time.Duration;
import java.util.*;
import java.util.concurrent.atomic.AtomicReference;
import com.finsight.config.WebClientConfig;
import com.finsight.config.AgentProperties;
import com.sun.net.httpserver.HttpServer;

import static org.assertj.core.api.Assertions.*;

class AgentClientFinancialTest {
    @Test void financialOperationsUseEtlClientAndSerializeOnlySelectedCandidates() {
        var path = new AtomicReference<String>(); var body = new AtomicReference<String>();
        WebClient forbiddenChat = WebClient.builder().exchangeFunction(request -> {
            throw new AssertionError("Financial operation used the chat client");
        }).build();
        WebClient etl = WebClient.builder().exchangeFunction(request -> {
            path.set(request.url().getPath());
            var output = new MockClientHttpRequest(request.method(), request.url());
            request.body().insert(output, new BodyInserter.Context() {
                public List<HttpMessageWriter<?>> messageWriters() { return ExchangeStrategies.withDefaults().messageWriters(); }
                public Optional<ServerHttpRequest> serverRequest() { return Optional.empty(); }
                public Map<String,Object> hints() { return Map.of(); }
            }).block();
            body.set(output.getBodyAsString().block());
            return Mono.just(ClientResponse.create(HttpStatus.OK).header("Content-Type", "application/json")
                    .body("{\"review_status\":\"WAITING\"}").build());
        }).build();
        var client = new AgentClient(forbiddenChat, etl);
        String id = "a".repeat(64);
        client.financialCandidate(id);
        assertThat(path.get()).isEqualTo("/internal/financial-submissions/" + id);
        client.reviewFinancialCandidate(id, Map.of("decision", "reject", "reason", "invalid", "operator_id", 11L));
        assertThat(path.get()).isEqualTo("/internal/financial-submissions/" + id + "/review");
        assertThat(body.get()).contains("\"operator_id\":11", "\"decision\":\"reject\"");
        client.publishFinancialCandidates(List.of(id), 11L);
        assertThat(path.get()).isEqualTo("/internal/financial-submissions/publish");
        assertThat(body.get()).contains("\"candidate_ids\":[\"" + id + "\"]", "\"operator_id\":11");
    }

    @Test void financialPublicationHasIndependentLongEtlBudget() {
        WebClient delayed = WebClient.builder().exchangeFunction(request -> Mono.delay(Duration.ofMillis(30))
                .map(ignored -> ClientResponse.create(HttpStatus.OK).header("Content-Type", "application/json")
                        .body("{\"version\":\"v1\"}").build())).build();
        var client = new AgentClient(delayed, delayed);
        ReflectionTestUtils.setField(client, "etlRequestTimeout", Duration.ofSeconds(2));
        assertThat(client.publishFinancialCandidates(List.of("a".repeat(64)), 11L)).containsEntry("version", "v1");
    }

    @Test void emptyFinancialResponseIsNotAnApprovalReceipt() {
        WebClient empty = WebClient.builder().exchangeFunction(request -> Mono.just(
                ClientResponse.create(HttpStatus.OK).build())).build();
        var client = new AgentClient(empty, empty);
        assertThatThrownBy(() -> client.reviewFinancialCandidate("a".repeat(64), Map.of())).isInstanceOf(IllegalStateException.class);
    }

    @Test void candidateDetailsCanExceedDefaultSmallWebClientBuffer() throws Exception {
        byte[] body = ("{\"evidence\":\"" + "x".repeat(300_000) + "\"}").getBytes(java.nio.charset.StandardCharsets.UTF_8);
        var server = HttpServer.create(new java.net.InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/internal/financial-submissions/" + "a".repeat(64), exchange -> {
            exchange.getResponseHeaders().add("Content-Type", "application/json");
            exchange.sendResponseHeaders(200, body.length);
            exchange.getResponseBody().write(body); exchange.close();
        });
        server.start();
        try {
            var properties = new AgentProperties(); properties.setInternalApiToken("test-service-token");
            properties.setBaseUrl("http://127.0.0.1:" + server.getAddress().getPort());
            var configuration = new WebClientConfig(properties);
            var client = new AgentClient(configuration.agentWebClient(), configuration.etlWebClient());
            assertThat((String) client.financialCandidate("a".repeat(64)).get("evidence")).hasSize(300_000);
        } finally { server.stop(0); }
    }
}
