package com.finsight.chat;

import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.web.reactive.function.client.ClientResponse;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;
import java.util.Map;
import static org.junit.jupiter.api.Assertions.*;

class AgentClientTest {
    private AgentClient refused(String body) {
        WebClient client = WebClient.builder().exchangeFunction(request -> Mono.just(
            ClientResponse.create(HttpStatus.CONFLICT).header("Content-Type", MediaType.APPLICATION_JSON_VALUE)
                .body(body).build())).build();
        return new AgentClient(client, client);
    }
    @Test void explicitQueueRefusalIsTyped() {
        var error = assertThrows(AgentAdmissionException.class, () ->
            refused("{\"detail\":\"model_queue_full\"}").streamChat(Map.of(), "t").blockFirst());
        assertEquals("model_queue_full", error.reasonCode());
        assertTrue(error.userMessage().contains("队列已满"));
    }
    @Test void UnknownConflictCannotBeDeclaredAnUnstartedTask() {
        var error = assertThrows(RuntimeException.class, () ->
            refused("{\"detail\":\"unknown_conflict\"}").streamChat(Map.of(), "t").blockFirst());
        assertFalse(error instanceof AgentAdmissionException);
    }
}
