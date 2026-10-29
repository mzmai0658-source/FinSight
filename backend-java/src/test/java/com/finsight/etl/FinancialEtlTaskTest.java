package com.finsight.etl;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.finsight.common.api.ErrorCode;
import com.finsight.common.exception.BizException;
import com.finsight.common.mq.MqOutboxService;
import com.finsight.etl.entity.EtlTask;
import com.finsight.etl.mapper.EtlTaskMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.util.ReflectionTestUtils;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.Map;
import java.util.List;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class FinancialEtlTaskTest {
    @TempDir Path uploadDir;
    EtlTaskMapper mapper;
    MqOutboxService outbox;
    EtlTaskService service;
    ObjectMapper json = new ObjectMapper().findAndRegisterModules();

    @BeforeEach void setup() {
        mapper = mock(EtlTaskMapper.class);
        outbox = mock(MqOutboxService.class);
        service = new EtlTaskService(mapper, outbox, json);
        ReflectionTestUtils.setField(service, "uploadDir", uploadDir.toString());
        lenient().when(mapper.lockSubmissionOperations()).thenReturn(1);
        lenient().when(mapper.insert(any(EtlTask.class))).thenAnswer(call -> {
            ((EtlTask) call.getArgument(0)).setId(1L); return 1;
        });
    }

    @Test void rejectsRenamedAndMalformedPdfBeforeQueueing() {
        for (byte[] bytes : new byte[][] {"plain text".getBytes(StandardCharsets.US_ASCII),
                "%PDF-1.7\nnot a document\n%%EOF".getBytes(StandardCharsets.US_ASCII)}) {
            assertThatThrownBy(() -> service.submit(new MockMultipartFile("file", "report.pdf",
                    "application/pdf", bytes), "financial", 7L))
                    .isInstanceOfSatisfying(BizException.class,
                            e -> assertThat(e.getErrorCode()).isEqualTo(ErrorCode.BAD_REQUEST));
        }
        verifyNoInteractions(outbox);
        verify(mapper, never()).insert(any(EtlTask.class));
    }

    @Test void uploadStoresContentHashAndStartsUnpublishedProcessing() throws Exception {
        byte[] bytes = onePagePdf();
        var task = service.submit(new MockMultipartFile("file", "..\\..\\confidential.pdf",
                "application/pdf", bytes), "financial", 7L);
        var data = json.valueToTree(task);
        assertThat(data.path("reviewStatus").asText()).isEqualTo("PROCESSING");
        assertThat(data.path("sourceSha256").asText()).isEqualTo(
                HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)));
        assertThat(task.getFileName()).isEqualTo("confidential.pdf");
        assertThat(data.path("publishedVersion").isNull()).isTrue();
    }

    @Test void financialProcessingSuccessPersistsCandidateWithoutPublishing() {
        EtlTask task = new EtlTask(); task.setId(9L); task.setFileType("financial");
        task.setRunToken("attempt");
        when(mapper.update(eq(task), any())).thenReturn(1);
        String hash = "a".repeat(64);
        service.complete(task, Map.of("status", "success", "candidate_id", hash,
                "source_sha256", hash, "review_status", "WAITING", "company", "示例公司",
                "stock_code", "123456", "report_year", 2025, "report_period", "FY"));
        var data = json.valueToTree(task);
        assertThat(task.getStatus()).isEqualTo("SUCCESS");
        assertThat(data.path("candidateId").asText()).isEqualTo(hash);
        assertThat(data.path("sourceSha256").asText()).isEqualTo(hash);
        assertThat(data.path("reviewStatus").asText()).isEqualTo("WAITING");
        assertThat(data.path("company").asText()).isEqualTo("示例公司");
        assertThat(data.path("reportPeriod").asText()).isEqualTo("FY");
        assertThat(data.path("publishedVersion").isNull()).isTrue();
    }

    @Test void financialLegacySuccessWithoutCandidateCannotPassAsCandidateReady() {
        EtlTask task = new EtlTask(); task.setId(9L); task.setFileType("financial");
        task.setRunToken("attempt");
        assertThatThrownBy(() -> service.complete(task, Map.of("status", "success")))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("candidate");
        verify(mapper, never()).update(any(EtlTask.class), any());
    }

    @Test void processingDiagnosticsCannotExposePrivatePathsOrCredentials() {
        EtlTask task = new EtlTask(); task.setId(9L); task.setFileType("financial"); task.setRunToken("attempt");
        when(mapper.update(eq(task), any())).thenReturn(1);
        service.complete(task, Map.of("status", "failed", "message", "Invalid D:/private/original.pdf api_key=secret",
                "steps", List.of(Map.of("name", "extract", "status", "failed", "path", "D:/private/original.pdf",
                        "message", "token=hidden"))));
        assertThat(task.getMessage()).doesNotContain("private", "secret");
        assertThat(task.getStepsJson()).doesNotContain("private", "hidden", "\"path\"");
    }

    @Test void legacyAdminRetryCannotReprocessRejectedFinancialCandidate() {
        EtlTask task = new EtlTask(); task.setId(9L); task.setFileType("financial");
        task.setStatus("FAILED"); task.setReviewStatus("REJECTED"); task.setRetryable(true);
        when(mapper.selectById(9L)).thenReturn(task);
        assertThatThrownBy(() -> service.retry(9)).isInstanceOf(BizException.class);
        verify(mapper, never()).prepareRetry(anyLong());
        verifyNoInteractions(outbox);
    }

    @Test void recoverableFinancialRetryReturnsToProcessingReviewState() {
        EtlTask task = new EtlTask(); task.setId(9L); task.setFileType("financial");
        task.setStatus("PARTIAL"); task.setReviewStatus("WAITING");
        task.setStepsJson("{\"retryable_steps\":[\"index\"]}");
        when(mapper.selectById(9L)).thenReturn(task); when(mapper.prepareRetry(9)).thenReturn(1);
        assertThat(service.retry(9).getReviewStatus()).isEqualTo("PROCESSING");
    }

    @Test void pdfParserRejectsEncryptedDocumentBeforeQueueing() throws Exception {
        var output = new ByteArrayOutputStream();
        try (var document = new org.apache.pdfbox.pdmodel.PDDocument()) {
            document.addPage(new org.apache.pdfbox.pdmodel.PDPage());
            document.protect(new org.apache.pdfbox.pdmodel.encryption.StandardProtectionPolicy("owner-password", "",
                    new org.apache.pdfbox.pdmodel.encryption.AccessPermission()));
            document.save(output);
        }
        assertThatThrownBy(() -> service.submit(new MockMultipartFile("file", "encrypted.pdf", "application/pdf",
                output.toByteArray()), "financial", 7L)).isInstanceOf(BizException.class);
        verifyNoInteractions(outbox);
    }

    @Test void duplicatePublishedAssetSharesVerifiedReceiptWithoutPublishingAgain() {
        EtlTask task = new EtlTask(); task.setId(9L); task.setFileType("financial"); task.setRunToken("attempt");
        when(mapper.update(eq(task), any())).thenReturn(1);
        String hash = "a".repeat(64);
        service.complete(task, Map.of("status", "success", "candidate_id", hash, "source_sha256", hash,
                "review_status", "PUBLISHED", "published_version", "v-1", "publication",
                Map.of("version", "v-1", "reports", 1, "facts", 9, "collection", "financial-v-1")));
        assertThat(task.getReviewStatus()).isEqualTo("PUBLISHED");
        assertThat(task.getPublishedVersion()).isEqualTo("v-1");
        assertThat(task.getPublicationReceiptJson()).contains("v-1", hash);
        verifyNoInteractions(outbox);
    }

    @Test void arbitraryProcessingStatusCannotGrantApproval() {
        EtlTask task = new EtlTask(); task.setId(9L); task.setFileType("financial"); task.setRunToken("attempt");
        when(mapper.update(eq(task), any())).thenReturn(1);
        String hash = "a".repeat(64);
        service.complete(task, Map.of("status", "success", "candidate_id", hash, "source_sha256", hash,
                "review_status", "APPROVED"));
        assertThat(task.getReviewStatus()).isEqualTo("WAITING");
    }

    @Test void persistedAdministratorReviewAllowsDuplicateToShareApproval() {
        EtlTask task = new EtlTask(); task.setId(9L); task.setFileType("financial"); task.setRunToken("attempt");
        when(mapper.update(eq(task), any())).thenReturn(1);
        String hash = "a".repeat(64);
        service.complete(task, Map.of("status", "success", "candidate_id", hash, "source_sha256", hash,
                "review_status", "APPROVED", "review", Map.of("decision", "approve", "operator_id", 2)));
        assertThat(task.getReviewStatus()).isEqualTo("APPROVED");
    }

    @Test void latestLocallyReviewedTwinWinsOverAnEarlierPipelineSnapshot() {
        EtlTask task = new EtlTask(); task.setId(9L); task.setFileType("financial"); task.setRunToken("attempt");
        String hash = "a".repeat(64);
        EtlTask twin = new EtlTask(); twin.setReviewStatus("REJECTED"); twin.setCandidateId(hash); twin.setSourceSha256(hash);
        when(mapper.selectOne(any())).thenReturn(twin); when(mapper.update(eq(task), any())).thenReturn(1);
        service.complete(task, Map.of("status", "success", "candidate_id", hash, "source_sha256", hash,
                "review_status", "APPROVED", "review", Map.of("decision", "approve", "operator_id", 2)));
        assertThat(task.getReviewStatus()).isEqualTo("REJECTED");
    }

    @Test void reprocessingAfterCodeChangeCannotInheritOldTwinApproval() {
        com.baomidou.mybatisplus.core.metadata.TableInfoHelper.initTableInfo(
                new org.apache.ibatis.builder.MapperBuilderAssistant(new com.baomidou.mybatisplus.core.MybatisConfiguration(), "test"), EtlTask.class);
        EtlTask task = new EtlTask(); task.setId(9L); task.setFileType("financial"); task.setRunToken("attempt");
        String hash = "a".repeat(64);
        EtlTask twin = new EtlTask(); twin.setReviewStatus("APPROVED");
        when(mapper.selectOne(any())).thenReturn(twin); when(mapper.update(eq(task), any())).thenReturn(1);
        service.complete(task, Map.of("status", "success", "candidate_id", hash, "source_sha256", hash,
                "review_status", "WAITING", "approval_invalidated", true));
        assertThat(task.getReviewStatus()).isEqualTo("WAITING");
        verify(mapper,never()).selectOne(any());
    }

    @Test void legacyAdminViewAlsoRedactsStoredFinancialDiagnostics() throws Exception {
        EtlTask task = new EtlTask(); task.setId(9L); task.setFileType("financial");
        task.setFileName("D:\\private\\original.pdf"); task.setMessage("Read failed /tmp/private/original.pdf token=secret");
        task.setStepsJson("{\"message\":\"api_key=hidden /tmp/private/original.pdf\"}");
        String view = json.writeValueAsString(AdminEtlController.TaskView.from(task));
        assertThat(view).contains("original.pdf").doesNotContain("private", "secret", "hidden");
    }

    @Test void invalidPublishedReceiptCannotMakeCandidatePublic() {
        EtlTask task = new EtlTask(); task.setId(9L); task.setFileType("financial"); task.setRunToken("attempt");
        String hash = "a".repeat(64);
        assertThatThrownBy(() -> service.complete(task, Map.of("status", "success", "candidate_id", hash, "source_sha256", hash,
                "review_status", "PUBLISHED", "published_version", "v-2", "publication",
                Map.of("version", "v-1", "reports", 1, "facts", 9, "collection", "financial-v-1"))))
                .isInstanceOf(IllegalStateException.class);
        assertThat(task.getReviewStatus()).isNotEqualTo("PUBLISHED");
        verify(mapper, never()).update(any(EtlTask.class), any());
    }

    /** A self-contained, valid one-page PDF without network or paid services. */
    public static byte[] onePagePdf() throws Exception {
        var output = new ByteArrayOutputStream();
        output.write("%PDF-1.4\n".getBytes(StandardCharsets.US_ASCII));
        int[] offsets = new int[4];
        String[] objects = {"", "<< /Type /Catalog /Pages 2 0 R >>",
                "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>"};
        for (int i = 1; i < objects.length; i++) {
            offsets[i] = output.size();
            output.write((i + " 0 obj\n" + objects[i] + "\nendobj\n").getBytes(StandardCharsets.US_ASCII));
        }
        int xref = output.size();
        output.write("xref\n0 4\n0000000000 65535 f \n".getBytes(StandardCharsets.US_ASCII));
        for (int i = 1; i < offsets.length; i++) {
            output.write(String.format("%010d 00000 n \n", offsets[i]).getBytes(StandardCharsets.US_ASCII));
        }
        output.write(("trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n" + xref + "\n%%EOF\n")
                .getBytes(StandardCharsets.US_ASCII));
        return output.toByteArray();
    }
}
