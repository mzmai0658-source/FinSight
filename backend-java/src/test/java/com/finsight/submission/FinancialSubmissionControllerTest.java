package com.finsight.submission;

import com.finsight.auth.LoginUser;
import com.finsight.auth.jwt.JwtAuthenticationFilter;
import com.finsight.config.SecurityConfig;
import com.finsight.etl.entity.EtlTask;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletRequest;
import jakarta.servlet.ServletResponse;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import java.util.List;
import java.util.Map;

import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.authentication;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

@WebMvcTest(controllers = {FinancialSubmissionController.class, AdminFinancialSubmissionController.class})
@Import(SecurityConfig.class)
@TestPropertySource(properties = "finsight.cors.allowed-origins=http://localhost")
class FinancialSubmissionControllerTest {
    @Autowired MockMvc mvc;
    @MockitoBean FinancialSubmissionService service;
    @MockitoBean JwtAuthenticationFilter jwtFilter;

    @BeforeEach void filter() throws Exception {
        doAnswer(call -> {
            ((FilterChain) call.getArgument(2)).doFilter((ServletRequest) call.getArgument(0), (ServletResponse) call.getArgument(1));
            return null;
        }).when(jwtFilter).doFilter(any(), any(), any());
    }

    @Test void anonymousSubmissionRoutesAreRejectedBeforeService() throws Exception {
        mvc.perform(get("/api/financial-submissions")).andExpect(status().isUnauthorized()).andExpect(jsonPath("$.code").value(40100));
        mvc.perform(post("/api/financial-submissions/1/retry")).andExpect(status().isUnauthorized());
        mvc.perform(get("/api/financial-submissions/1/original")).andExpect(status().isUnauthorized());
        verifyNoInteractions(service);
    }

    @Test void regularAccountCannotInvokeAdministrativeRoutes() throws Exception {
        mvc.perform(get("/api/admin/financial-submissions").with(user("USER"))).andExpect(status().isForbidden());
        mvc.perform(get("/api/admin/financial-submissions/1/candidate").with(user("USER"))).andExpect(status().isForbidden());
        mvc.perform(post("/api/admin/financial-submissions/1/review").with(user("USER")).contentType(MediaType.APPLICATION_JSON)
                .content("{\"decision\":\"reject\",\"reason\":\"bad source\"}")).andExpect(status().isForbidden());
        mvc.perform(post("/api/admin/financial-submissions/publish").with(user("USER")).contentType(MediaType.APPLICATION_JSON)
                .content("{\"ids\":[1]}")).andExpect(status().isForbidden());
        verifyNoInteractions(service);
    }

    @Test void multipartUploadAndOwnerListFollowPublicEnvelopeContract() throws Exception {
        var view = view();
        when(service.submit(any())).thenReturn(view);
        when(service.mine(1,20)).thenReturn(new FinancialSubmissionDtos.PageView(1,1,List.of(view)));
        mvc.perform(multipart("/api/financial-submissions").file(new MockMultipartFile("file", "report.pdf", "application/pdf", new byte[]{1}))
                .with(user("USER"))).andExpect(status().isOk()).andExpect(jsonPath("$.data.reviewStatus").value("PROCESSING"))
                .andExpect(jsonPath("$.data.filePath").doesNotExist()).andExpect(jsonPath("$.data.createdBy").doesNotExist());
        mvc.perform(get("/api/financial-submissions").with(user("ADMIN"))).andExpect(status().isOk())
                .andExpect(jsonPath("$.data.records[0].id").value(1));
        verify(service).mine(1,20);
    }

    @Test void detailRetryAndOriginalRoutesReachOwnerScopedService() throws Exception {
        when(service.get(1)).thenReturn(view()); when(service.retry(1)).thenReturn(view());
        when(service.original(1)).thenReturn(ResponseEntity.ok().contentType(MediaType.APPLICATION_PDF)
                .body(new ByteArrayResource("%PDF-1.4".getBytes())));
        mvc.perform(get("/api/financial-submissions/1").with(user("USER"))).andExpect(status().isOk()).andExpect(jsonPath("$.data.id").value(1));
        mvc.perform(post("/api/financial-submissions/1/retry").with(user("USER"))).andExpect(status().isOk());
        mvc.perform(get("/api/financial-submissions/1/original").with(user("USER"))).andExpect(status().isOk())
                .andExpect(content().contentType(MediaType.APPLICATION_PDF)).andExpect(content().string("%PDF-1.4"));
    }

    @Test void adminCandidateReviewAndSelectedPublishRoutesUseAgreedJsonFields() throws Exception {
        when(service.adminPage(1,20,"WAITING")).thenReturn(new FinancialSubmissionDtos.PageView(0,1,List.of()));
        when(service.candidate(1)).thenReturn(Map.of("candidate_id", "a".repeat(64)));
        when(service.review(eq(1L), any())).thenReturn(view());
        when(service.publish(any())).thenReturn(Map.of("version", "v1", "reports", 1, "facts", 2, "collection", "financial-v1"));
        mvc.perform(get("/api/admin/financial-submissions").param("reviewStatus","WAITING").with(user("ADMIN")))
                .andExpect(status().isOk()).andExpect(jsonPath("$.data.records").isArray());
        mvc.perform(get("/api/admin/financial-submissions/1/candidate").with(user("ADMIN"))).andExpect(status().isOk())
                .andExpect(jsonPath("$.data.candidate_id").value("a".repeat(64)));
        mvc.perform(post("/api/admin/financial-submissions/1/review").with(user("ADMIN")).contentType(MediaType.APPLICATION_JSON)
                .content("{\"decision\":\"approve\",\"identity\":{\"stock_code\":\"123456\",\"company\":\"Example\",\"year\":2025,\"period\":\"FY\",\"aliases\":[]},\"operator_id\":999}"))
                .andExpect(status().isOk());
        verify(service).review(1, new FinancialSubmissionDtos.ReviewRequest("approve", null,
                new FinancialSubmissionDtos.Identity("123456", "Example",2025,"FY",List.of())));
        mvc.perform(post("/api/admin/financial-submissions/publish").with(user("ADMIN")).contentType(MediaType.APPLICATION_JSON)
                .content("{\"ids\":[1,2]}")).andExpect(status().isOk()).andExpect(jsonPath("$.data.version").value("v1"));
        verify(service).publish(new FinancialSubmissionDtos.PublishRequest(List.of(1L,2L)));
    }

    static org.springframework.test.web.servlet.request.RequestPostProcessor user(String role) {
        return authentication(new UsernamePasswordAuthenticationToken(new LoginUser(7,"actor",role,"test"),null,
                List.of(new SimpleGrantedAuthority("ROLE_"+role))));
    }
    static FinancialSubmissionDtos.SubmissionView view() {
        EtlTask task = new EtlTask(); task.setId(1L); task.setFileName("report.pdf");task.setStatus("PENDING");task.setReviewStatus("PROCESSING");
        return FinancialSubmissionService.view(task);
    }
}
