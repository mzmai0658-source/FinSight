package com.finsight.report;

import com.finsight.auth.LoginUser;
import com.finsight.common.exception.BizException;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.web.reactive.function.client.ClientResponse;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;
import java.util.List;
import java.util.concurrent.atomic.AtomicReference;
import java.nio.charset.StandardCharsets;
import static org.junit.jupiter.api.Assertions.*;

class FinancialMaterialControllerTest {
    @AfterEach void clear() { SecurityContextHolder.clearContext(); }
    void login() {
        SecurityContextHolder.getContext().setAuthentication(new UsernamePasswordAuthenticationToken(
            new LoginUser(7, "reader", "USER", "test"), null, List.of()));
    }
    @Test void requiresLoginBeforeContactingAgent() {
        var controller = new FinancialMaterialController(WebClient.builder().exchangeFunction(r -> {
            fail("Anonymous request reached agent"); return Mono.empty();
        }).build());
        assertThrows(BizException.class, () -> controller.list(1, 12, ""));
        assertThrows(BizException.class, () -> controller.file("600080", 2023, "HY"));
        assertThrows(BizException.class, () -> controller.pages(1,1));
    }
    @Test void validatesIdentityAndPaginationBeforeContactingAgent() {
        login();
        var controller = new FinancialMaterialController(WebClient.builder().exchangeFunction(r -> {
            fail("Invalid request reached agent"); return Mono.empty();
        }).build());
        assertThrows(BizException.class, () -> controller.list(1, 51, ""));
        assertThrows(BizException.class, () -> controller.file("../secret", 2023, "HY"));
        assertThrows(BizException.class, () -> controller.file("600080", 2023, "invalid"));
        assertThrows(BizException.class, () -> controller.pages(1,0));
    }
    @Test void proxiesPdfBytesAndLengthForAuthenticatedReader() throws Exception {
        login();
        var uri = new AtomicReference<String>();
        var controller = new FinancialMaterialController(WebClient.builder().exchangeFunction(r -> {
            uri.set(r.url().getPath());
            return Mono.just(ClientResponse.create(HttpStatus.OK).header("Content-Type", "application/pdf")
                .header("Content-Length", "8").body("%PDF-1.4").build());
        }).build());
        var result = controller.file("600080", 2023, "HY");
        assertEquals("/internal/materials/financial/600080/2023/HY/file", uri.get());
        assertEquals("%PDF-1.4", new String(result.getBody(),StandardCharsets.US_ASCII));
        assertEquals(8, result.getHeaders().getContentLength());
        assertEquals(MediaType.APPLICATION_PDF, result.getHeaders().getContentType());
        assertEquals("private, no-store", result.getHeaders().getCacheControl());
        assertNull(result.getHeaders().getFirst("Content-Disposition"));
        assertArrayEquals(result.getBody(),controller.download("600080",2023,"HY").getBody());
    }
    @Test void encodesKeywordWithoutAddingExtraQueryParameters() {
        login();
        var uri = new AtomicReference<String>();
        var controller = new FinancialMaterialController(WebClient.builder().exchangeFunction(r -> {
            uri.set(r.url().toString());
            return Mono.just(ClientResponse.create(HttpStatus.OK).header("Content-Type", "application/json")
                .body("{\"records\":[],\"total\":0,\"page\":1}").build());
        }).build());
        controller.list(1, 12, "金花&size=50");
        assertFalse(uri.get().contains("&size=50"));
        assertTrue(uri.get().contains("%26size%3D50"));
    }

    @Test void rejectsIncompleteOrNonPdfBeforeReturningPdfHeaders() {
        login();
        for (var spec:List.of(new String[]{"application/pdf","9","%PDF-1.4"},
                new String[]{"application/json","8","%PDF-1.4"},new String[]{"application/pdf","8","not a pdf"})) {
            var controller=new FinancialMaterialController(WebClient.builder().exchangeFunction(r ->
                Mono.just(ClientResponse.create(HttpStatus.OK).header("Content-Type",spec[0])
                    .header("Content-Length",spec[1]).body(spec[2]).build())).build());
            assertThrows(BizException.class,()->controller.download("600080",2023,"HY"));
        }
    }
}
