package com.finsight.config;

import com.finsight.advisor.AdvisorController;
import com.finsight.advisor.mq.AdvisorReportConsumer;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.http.HttpStatus;
import org.springframework.web.reactive.function.client.ClientResponse;
import reactor.core.publisher.Mono;
import static org.junit.jupiter.api.Assertions.*;

class InternalServiceConfigTest {
    @Test void requiresAnExplicitSharedToken() {
        var config = new WebClientConfig(new AgentProperties());
        assertThrows(IllegalStateException.class, config::agentWebClient);
        assertThrows(IllegalStateException.class, config::etlWebClient);
    }
    @Test void bothClientsSendServiceIdentity() {
        var properties = new AgentProperties(); properties.setInternalApiToken("test-service-token");
        var config = new WebClientConfig(properties);
        for (var client : java.util.List.of(config.agentWebClient(), config.etlWebClient())) {
            client.mutate().exchangeFunction(request -> {
                assertEquals("test-service-token", request.headers().getFirst("X-Internal-Token"));
                return Mono.just(ClientResponse.create(HttpStatus.OK).body("ok").build());
            }).build().get().uri("/internal/health").retrieve().bodyToMono(String.class).block();
        }
    }
    @Test void unverifiedAdvisorEndpointsAndWorkersAreDisabledByDefault() {
        new ApplicationContextRunner().withUserConfiguration(AdvisorController.class, AdvisorReportConsumer.class)
                .run(context -> {
                    assertFalse(context.containsBean("advisorController"));
                    assertTrue(context.getBeansOfType(AdvisorController.class).isEmpty());
                    assertTrue(context.getBeansOfType(AdvisorReportConsumer.class).isEmpty());
                });
    }
}
