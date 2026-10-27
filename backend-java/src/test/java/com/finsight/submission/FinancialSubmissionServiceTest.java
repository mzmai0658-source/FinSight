package com.finsight.submission;

import com.baomidou.mybatisplus.core.MybatisConfiguration;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.metadata.TableInfoHelper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.finsight.auth.LoginUser;
import com.finsight.chat.AgentClient;
import com.finsight.common.api.ErrorCode;
import com.finsight.common.exception.BizException;
import com.finsight.etl.EtlTaskService;
import com.finsight.etl.entity.EtlTask;
import com.finsight.etl.mapper.EtlTaskMapper;
import org.apache.ibatis.builder.MapperBuilderAssistant;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;
import org.mockito.ArgumentCaptor;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.test.util.ReflectionTestUtils;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class FinancialSubmissionServiceTest {
    @TempDir Path uploadDir;
    EtlTaskMapper mapper;
    EtlTaskService etl;
    AgentClient agent;
    StringRedisTemplate redis;
    FinancialSubmissionService service;
    ObjectMapper json = new ObjectMapper().findAndRegisterModules();
    String hash = "b".repeat(64);

    @BeforeAll static void tableInfo() {
        TableInfoHelper.initTableInfo(new MapperBuilderAssistant(new MybatisConfiguration(), "test"), EtlTask.class);
    }

    @BeforeEach void setup() {
        mapper = mock(EtlTaskMapper.class); etl = mock(EtlTaskService.class);
        agent = mock(AgentClient.class); redis = mock(StringRedisTemplate.class);
        service = new FinancialSubmissionService(mapper, etl, agent, json, redis);
        ReflectionTestUtils.setField(service, "uploadDir", uploadDir.toString());
        login(7, "USER");
    }

    @AfterEach void logout() { SecurityContextHolder.clearContext(); }

    @Test void ordinaryOperationsRequireAuthentication() {
        logout();
        assertCode(ErrorCode.UNAUTHORIZED, () -> service.submit(new MockMultipartFile("file", new byte[]{1})));
        assertCode(ErrorCode.UNAUTHORIZED, () -> service.mine(1, 20));
        assertCode(ErrorCode.UNAUTHORIZED, () -> service.get(1));
        assertCode(ErrorCode.UNAUTHORIZED, () -> service.retry(1));
        assertCode(ErrorCode.UNAUTHORIZED, () -> service.original(1));
        verifyNoInteractions(mapper, etl, agent);
    }

    @Test void differentOwnerCannotReadRetryOrDownload() {
        var task = task(1, 9, "FAILED", "PROCESSING");
        when(mapper.selectById(1L)).thenReturn(task);
        when(mapper.lockFinancialSubmission(1L)).thenReturn(task);
        assertCode(ErrorCode.FORBIDDEN, () -> service.get(1));
        assertCode(ErrorCode.FORBIDDEN, () -> service.retry(1));
        assertCode(ErrorCode.FORBIDDEN, () -> service.original(1));
        verifyNoInteractions(etl, agent);
    }

    @Test void ordinaryUserCannotReachAnyAdministrativeOperation() {
        assertCode(ErrorCode.FORBIDDEN, () -> service.adminPage(1, 20, "WAITING"));
        assertCode(ErrorCode.FORBIDDEN, () -> service.candidate(1));
        assertCode(ErrorCode.FORBIDDEN, () -> service.review(1, approval()));
        assertCode(ErrorCode.FORBIDDEN, () -> service.publish(new FinancialSubmissionDtos.PublishRequest(List.of(1L))));
        verifyNoInteractions(mapper, etl, agent);
    }

    @Test void ownerListAlwaysFiltersByCurrentUserIncludingAdministrators() {
        login(11, "ADMIN");
        when(mapper.selectPage(any(Page.class), any())).thenAnswer(call -> call.getArgument(0));
        service.mine(1, 20);
        var capture = ArgumentCaptor.forClass(LambdaQueryWrapper.class);
        verify(mapper).selectPage(any(Page.class), capture.capture());
        assertThat(capture.getValue().getSqlSegment()).contains("created_by", "file_type");
        assertThat(capture.getValue().getParamNameValuePairs().values()).contains(11L, "financial");
    }

    @Test void viewExcludesOwnershipPathsAndInternalExecutionState() throws Exception {
        var task = task(1, 7, "SUCCESS", "WAITING");
        task.setFilePath("D:/private/source.pdf"); task.setRunToken("secret-attempt");
        task.setMessage("failed at D:\\private\\source.pdf using api_key=secret-key");
        when(mapper.selectById(1L)).thenReturn(task);
        var result = json.writeValueAsString(service.get(1));
        assertThat(result).contains("candidateId", "reviewStatus").doesNotContain("createdBy", "filePath", "runToken",
                "private", "secret-key");
    }

    @Test void originalIsOwnerScopedBlobPreviewAndCannotEscapeStorage() throws Exception {
        var task = task(1, 7, "SUCCESS", "WAITING");
        Path pdf = uploadDir.resolve("report.pdf"); Files.writeString(pdf, "%PDF-1.4");
        task.setFilePath(pdf.toString()); when(mapper.selectById(1L)).thenReturn(task);
        var response = service.original(1);
        assertThat(response.getHeaders().getContentType().toString()).isEqualTo("application/pdf");
        assertThat(response.getHeaders().containsKey("Content-Disposition")).isFalse();
        assertThat(response.getHeaders().getCacheControl()).isEqualTo("private, no-store");
        assertThat(response.getHeaders().getFirst("X-Content-Type-Options")).isEqualTo("nosniff");
        assertThat(response.getBody().getInputStream().readAllBytes()).isEqualTo("%PDF-1.4".getBytes());
        task.setFilePath(uploadDir.resolve("..").resolve("other.pdf").normalize().toString());
        assertCode(ErrorCode.NOT_FOUND, () -> service.original(1));
    }

    @Test void retryOnlyAllowsRecoverableProcessingFailure() {
        var task = task(1, 7, "FAILED", "PROCESSING"); task.setRetryable(true);
        when(mapper.lockFinancialSubmission(1L)).thenReturn(task);
        when(etl.retry(1L)).thenReturn(task);
        service.retry(1);
        verify(etl).retry(1L);
        task.setReviewStatus("REJECTED");
        assertCode(ErrorCode.BAD_REQUEST, () -> service.retry(1));
        task.setReviewStatus("PROCESSING"); task.setRetryable(false); task.setStepsJson("{\"retryable_steps\":[]}");
        assertCode(ErrorCode.BAD_REQUEST, () -> service.retry(1));
    }

    @Test void reviewValidatesIdentityAndReasonBeforeContactingPython() {
        login(2, "ADMIN");
        assertCode(ErrorCode.BAD_REQUEST, () -> service.review(1, new FinancialSubmissionDtos.ReviewRequest("approve", "", null)));
        assertCode(ErrorCode.BAD_REQUEST, () -> service.review(1, new FinancialSubmissionDtos.ReviewRequest("reject", "  ", null)));
        verifyNoInteractions(mapper, agent);
    }

    @Test void pythonReviewFailureDoesNotApproveOrPersistAnyReview() {
        login(2, "ADMIN");
        var task = lockedTask("SUCCESS", "WAITING");
        when(agent.reviewFinancialCandidate(eq(hash), anyMap())).thenThrow(new IllegalStateException("D:/private/path api_key=secret"));
        assertCode(ErrorCode.AGENT_UNAVAILABLE, () -> service.review(1, approval()));
        assertThat(task.getReviewStatus()).isEqualTo("WAITING");
        verify(mapper, never()).update(any(), any());
    }

    @Test void successfulApprovalUsesTrustedOperatorAndSynchronizesCandidateRows() {
        login(2, "ADMIN"); var task = lockedTask("SUCCESS", "WAITING");
        when(agent.reviewFinancialCandidate(eq(hash), anyMap())).thenReturn(candidateResponse("APPROVED"));
        when(mapper.update(isNull(), any())).thenReturn(2);
        var result = service.review(1, approval());
        assertThat(result.reviewStatus()).isEqualTo("APPROVED");
        assertThat(result.company()).isEqualTo("示例公司");
        var body = ArgumentCaptor.forClass(Map.class);
        verify(agent).reviewFinancialCandidate(eq(hash), body.capture());
        assertThat(body.getValue().get("operator_id")).isEqualTo(2L);
        verify(mapper).lockSubmissionOperations();
        verify(mapper).update(isNull(), any());
    }

    @Test void failedCandidateCanBeRejectedWithoutApprovingIt() {
        login(2, "ADMIN"); lockedTask("FAILED", "PROCESSING");
        when(agent.reviewFinancialCandidate(eq(hash), anyMap())).thenReturn(candidateResponse("REJECTED"));
        when(mapper.update(isNull(), any())).thenReturn(1);
        assertThat(service.review(1, new FinancialSubmissionDtos.ReviewRequest("reject", "无法核验原件", null)).reviewStatus())
                .isEqualTo("REJECTED");
    }

    @Test void publishRequiresEachSelectedCandidateApprovedBeforeCallingPython() {
        login(2, "ADMIN"); lockedTask("SUCCESS", "WAITING");
        assertCode(ErrorCode.BAD_REQUEST, () -> service.publish(new FinancialSubmissionDtos.PublishRequest(List.of(1L))));
        verifyNoInteractions(agent);
    }

    @Test void failedPublishNeverMarksPublishedOrEvictsCaches() {
        login(2, "ADMIN"); var task = lockedTask("SUCCESS", "APPROVED");
        when(agent.publishFinancialCandidates(anyList(), eq(2L))).thenThrow(new IllegalStateException("remote failed"));
        assertCode(ErrorCode.AGENT_UNAVAILABLE, () -> service.publish(new FinancialSubmissionDtos.PublishRequest(List.of(1L))));
        assertThat(task.getReviewStatus()).isEqualTo("APPROVED");
        verify(mapper, never()).update(any(), any());
        verifyNoInteractions(redis);
    }

    @Test void selectedBatchPublishesOnceAndReplaysReceiptIdempotently() {
        login(2, "ADMIN"); var task = lockedTask("SUCCESS", "APPROVED");
        var receipt = Map.<String, Object>of("version", "v-1", "reports", 1, "facts", 5, "collection", "financial-v-1");
        when(agent.publishFinancialCandidates(List.of(hash), 2L)).thenReturn(receipt);
        when(mapper.update(isNull(), any())).thenReturn(1);
        var request = new FinancialSubmissionDtos.PublishRequest(List.of(1L, 1L));
        assertThat(service.publish(request)).isEqualTo(receipt);
        assertThat(task.getReviewStatus()).isEqualTo("PUBLISHED");
        assertThat(task.getPublishedVersion()).isEqualTo("v-1");
        assertThat(service.publish(request)).isEqualTo(receipt);
        verify(agent, times(1)).publishFinancialCandidates(List.of(hash), 2L);
        verify(redis).delete("market:summary");
    }

    @Test void candidateProxyDropsInternalPathsAndOwnerMetadataRecursively() {
        login(2, "ADMIN"); var task = task(1, 7, "SUCCESS", "WAITING");
        when(mapper.selectById(1L)).thenReturn(task);
        var response = new LinkedHashMap<>(candidateResponse("WAITING"));
        response.put("file_path", "D:/private/upload.pdf"); response.put("created_by", 7);
        response.put("steps", List.of(Map.of("name", "extract", "status", "failed", "message", "D:/private/token.txt api_key=secret")));
        when(agent.financialCandidate(hash)).thenReturn(response);
        String result = json.valueToTree(service.candidate(1)).toString();
        assertThat(result).contains("candidate_id", "identity").doesNotContain("file_path", "created_by", "private", "secret");
    }

    @Test void evidenceSanitizingPreservesFinancialUnitsAndQuotations() {
        String quote = "基本每股收益 1.25 元/股，净利润 100 万元";
        assertThat(FinancialSubmissionData.text(quote)).isEqualTo(quote);
    }

    @Test void evidenceSanitizingPreservesPublicHttpsSourceUrls() {
        String source = "https://company.example/reports/2025-annual.pdf";
        assertThat(FinancialSubmissionData.text(source)).isEqualTo(source);
    }

    EtlTask lockedTask(String status, String review) {
        var task = task(1, 7, status, review);
        when(mapper.lockSubmissionOperations()).thenReturn(1);
        when(mapper.lockFinancialSubmission(1L)).thenReturn(task);
        return task;
    }
    EtlTask task(long id, long owner, String status, String review) {
        var task = new EtlTask(); task.setId(id); task.setCreatedBy(owner); task.setFileType("financial");
        task.setTaskUid("uid"); task.setFileName("report.pdf"); task.setStatus(status); task.setReviewStatus(review);
        task.setCandidateId(hash); task.setSourceSha256(hash); return task;
    }
    Map<String, Object> candidateResponse(String status) {
        return Map.of("candidate_id", hash, "source_sha256", hash, "review_status", status,
                "identity", Map.of("stock_code", "123456", "company", "示例公司", "year", 2025, "period", "FY", "aliases", List.of()));
    }
    FinancialSubmissionDtos.ReviewRequest approval() {
        return new FinancialSubmissionDtos.ReviewRequest("approve", "已核验", new FinancialSubmissionDtos.Identity(
                "123456", "示例公司", 2025, "FY", List.of()));
    }
    static void login(long id, String role) {
        SecurityContextHolder.getContext().setAuthentication(new UsernamePasswordAuthenticationToken(
                new LoginUser(id, "actor", role, "test"), null, List.of()));
    }
    static void assertCode(ErrorCode code, org.assertj.core.api.ThrowableAssert.ThrowingCallable call) {
        assertThatThrownBy(call).isInstanceOfSatisfying(BizException.class, e -> assertThat(e.getErrorCode()).isEqualTo(code));
    }
}
