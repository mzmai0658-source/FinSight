package com.finsight.report;

import com.finsight.auth.LoginUser;
import com.finsight.common.exception.BizException;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.web.reactive.function.client.ClientResponse;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;
import java.util.List;
import static org.junit.jupiter.api.Assertions.*;

class ResearchMaterialControllerTest {
    @AfterEach void clear() { SecurityContextHolder.clearContext(); }
    void login() { SecurityContextHolder.getContext().setAuthentication(new UsernamePasswordAuthenticationToken(
        new LoginUser(7,"reader","USER","test"),null,List.of())); }
    @Test void anonymousAndInvalidRequestsNeverReachAgent() {
        var controller=new ResearchMaterialController(WebClient.builder().exchangeFunction(r->{fail("Unexpected upstream request");return Mono.empty();}).build());
        assertThrows(BizException.class,()->controller.pages(7,1));
        login();assertThrows(BizException.class,()->controller.pages(-1,1));assertThrows(BizException.class,()->controller.pages(7,0));
    }
    @Test void requestsTheSelectedPageAndRejectsMissingPages() {
        login();
        var controller=new ResearchMaterialController(WebClient.builder().exchangeFunction(r->{
            assertEquals("/internal/materials/research/7/pages",r.url().getPath());assertEquals("page=2",r.url().getQuery());
            return Mono.just(ClientResponse.create(HttpStatus.NOT_FOUND).build());
        }).build());
        assertThrows(BizException.class,()->controller.pages(7,2));
    }
}
