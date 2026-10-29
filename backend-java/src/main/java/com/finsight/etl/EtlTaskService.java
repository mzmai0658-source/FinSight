package com.finsight.etl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.finsight.common.api.ErrorCode;
import com.finsight.common.exception.BizException;
import com.finsight.common.mq.MqOutboxService;
import com.finsight.config.RabbitConfig;
import com.finsight.etl.entity.EtlTask;
import com.finsight.etl.mapper.EtlTaskMapper;
import com.finsight.etl.mq.EtlTaskMessage;
import com.finsight.submission.FinancialSubmissionData;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;
import org.apache.pdfbox.io.RandomAccessReadBuffer;
import org.apache.pdfbox.pdfparser.PDFParser;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.time.Duration;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.HexFormat;
import java.util.Locale;

@Slf4j
@Service
@RequiredArgsConstructor
public class EtlTaskService {

    public static final String STATUS_PENDING = "PENDING";
    public static final String STATUS_RUNNING = "RUNNING";
    public static final String STATUS_SUCCESS = "SUCCESS";
    public static final String STATUS_FAILED = "FAILED";
    public static final String STATUS_PARTIAL = "PARTIAL";

    private static final Set<String> FILE_TYPES = Set.of("financial", "research");

    private final EtlTaskMapper taskMapper;
    private final MqOutboxService mqOutboxService;
    private final ObjectMapper objectMapper;

    @Value("${finsight.etl.upload-dir}")
    private String uploadDir;

    @Transactional
    public EtlTask submit(MultipartFile file, String fileType, long userId) {
        if (file == null || file.isEmpty()) {
            throw new BizException(ErrorCode.BAD_REQUEST, "Please choose a PDF file");
        }
        String original = file.getOriginalFilename() == null ? "report.pdf" : file.getOriginalFilename();
        if (!original.toLowerCase(Locale.ROOT).endsWith(".pdf")) {
            throw new BizException(ErrorCode.BAD_REQUEST, "Only PDF files are supported");
        }
        if (!FILE_TYPES.contains(fileType)) {
            throw new BizException(ErrorCode.BAD_REQUEST, "fileType only supports financial / research");
        }

        boolean financial = "financial".equals(fileType);
        byte[] pdfBytes = financial ? validateFinancialPdf(file) : null;
        if (financial) {
            original = original.replace('\\', '/');
            original = original.substring(original.lastIndexOf('/') + 1).replaceAll("[\\p{Cntrl}]", "_");
            if (original.length() > 255) original = original.substring(original.length() - 255);
        }
        String taskUid = UUID.randomUUID().toString();
        Path target = financial ? storeFinancialFile(pdfBytes, taskUid) : storeFile(file, original, taskUid);

        EtlTask task = new EtlTask();
        task.setTaskUid(taskUid);
        task.setFileName(original);
        task.setFilePath(target.toAbsolutePath().toString());
        task.setFileType(fileType);
        task.setStatus(STATUS_PENDING);
        task.setMessage("");
        task.setStockCode("");
        task.setCreatedBy(userId);
        if (financial) {
            task.setReviewStatus("PROCESSING");
            task.setSourceSha256(sha256(pdfBytes));
        }
        requireWrite(taskMapper.insert(task), "create task");

        enqueue(task);
        log.info("[etl] task queued taskId={} file={}", task.getId(), original);
        return task;
    }

    private void enqueue(EtlTask task) {
        mqOutboxService.publish(RabbitConfig.EXCHANGE, RabbitConfig.ETL_TASK_RK,
                new EtlTaskMessage(task.getId()));
    }

    private Path storeFile(MultipartFile file, String original, String taskUid) {
        try {
            Path dir = Paths.get(uploadDir).toAbsolutePath().normalize()
                    .resolve(taskUid.substring(0, 8));
            Files.createDirectories(dir);
            Path target = dir.resolve(Paths.get(original).getFileName().toString());
            try (var in = file.getInputStream()) {
                Files.copy(in, target, StandardCopyOption.REPLACE_EXISTING);
            }
            return target;
        } catch (Exception e) {
            throw new BizException(ErrorCode.INTERNAL_ERROR, "File save failed");
        }
    }

    private Path storeFinancialFile(byte[] bytes, String taskUid) {
        try {
            Path dir = Paths.get(uploadDir).toAbsolutePath().normalize().resolve(taskUid);
            Files.createDirectories(dir);
            return Files.write(dir.resolve("original.pdf"), bytes, java.nio.file.StandardOpenOption.CREATE_NEW);
        } catch (Exception e) {
            throw new BizException(ErrorCode.INTERNAL_ERROR, "File save failed");
        }
    }

    private static byte[] validateFinancialPdf(MultipartFile file) {
        if (file.getSize() > 100L * 1024 * 1024) {
            throw new BizException(ErrorCode.BAD_REQUEST, "PDF must be at most 100 MiB");
        }
        try {
            byte[] bytes = file.getBytes();
            if (bytes.length < 5 || bytes[0] != '%' || bytes[1] != 'P' || bytes[2] != 'D'
                    || bytes[3] != 'F' || bytes[4] != '-') {
                throw new IllegalArgumentException("Invalid PDF signature");
            }
            try (var source = new RandomAccessReadBuffer(bytes)) {
                var parser = new PDFParser(source);
                try (var document = parser.parse(false)) {
                    if (document.isEncrypted() || document.getNumberOfPages() < 1) {
                        throw new IllegalArgumentException("Encrypted or empty PDF");
                    }
                }
            }
            return bytes;
        } catch (Exception e) {
            throw new BizException(ErrorCode.BAD_REQUEST, "Please upload a valid, unencrypted PDF with at least one page");
        }
    }

    private static String sha256(byte[] bytes) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
        } catch (java.security.NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 is unavailable", e);
        }
    }

    public boolean markRunning(EtlTask task) {
        String token = UUID.randomUUID().toString();
        LocalDateTime started = LocalDateTime.now();
        if (taskMapper.claim(task.getId(), token, started) != 1) {
            return false;
        }
        task.setStatus(STATUS_RUNNING);
        task.setRunToken(token);
        task.setStartedAt(started);
        task.setRetryable(false);
        task.setAttemptCount(task.getAttemptCount() == null ? 1 : task.getAttemptCount() + 1);
        if ("financial".equals(task.getFileType())) task.setReviewStatus("PROCESSING");
        return true;
    }

    @Transactional
    public void complete(EtlTask task, Map<String, Object> report) {
        if ("financial".equals(task.getFileType())) {
            if (taskMapper.lockSubmissionOperations() == null) {
                throw new IllegalStateException("Financial candidate operation lock is unavailable");
            }
            String candidateId = nullableString(report.get("candidate_id"));
            String sourceHash = nullableString(report.get("source_sha256"));
            boolean completed = Set.of("success", "partial").contains(String.valueOf(report.get("status")));
            if ((completed || candidateId != null) && (candidateId == null || !candidateId.matches("[a-f0-9]{64}")
                    || !candidateId.equals(sourceHash))) {
                throw new IllegalStateException("Financial candidate response is incomplete");
            }
            if (sourceHash != null && task.getSourceSha256() != null && !sourceHash.equals(task.getSourceSha256())) {
                throw new IllegalStateException("Financial candidate does not match the uploaded PDF");
            }
            task.setCandidateId(candidateId);
            if (sourceHash != null) task.setSourceSha256(sourceHash);
            task.setReviewStatus(completed ? "WAITING" : "PROCESSING");
            task.setCompany(nullableString(report.get("company")));
            task.setReportPeriod(nullableString(report.get("report_period")));
            if (!Boolean.TRUE.equals(report.get("approval_invalidated"))) acceptPersistedReview(task, report, completed);
        }
        task.setStatus(switch (String.valueOf(report.get("status"))) {
            case "success" -> STATUS_SUCCESS;
            case "partial" -> STATUS_PARTIAL;
            default -> STATUS_FAILED;
        });
        task.setMessage(taskMessage(task, String.valueOf(report.getOrDefault("message", ""))));
        task.setStockCode(String.valueOf(report.getOrDefault("stock_code", "")));
        Object year = report.get("report_year");
        if (year instanceof Number number) {
            task.setReportYear(number.intValue());
        }
        if ("financial".equals(task.getFileType()) && task.getCandidateId() != null) {
            // The lock orders completion against administrator operations. A locally
            // reviewed twin may be newer than the completed Python request's snapshot.
            if (Boolean.TRUE.equals(report.get("approval_invalidated"))) {
                taskMapper.update(null, new LambdaUpdateWrapper<EtlTask>()
                        .eq(EtlTask::getFileType, "financial").eq(EtlTask::getCandidateId, task.getCandidateId())
                        .eq(EtlTask::getSourceSha256, task.getSourceSha256())
                        .in(EtlTask::getReviewStatus, "APPROVED", "REJECTED")
                        .set(EtlTask::getReviewStatus, task.getReviewStatus())
                        .set(EtlTask::getPublishedVersion, null).set(EtlTask::getPublicationReceiptJson, null));
            } else {
            EtlTask twin = taskMapper.selectOne(new LambdaQueryWrapper<EtlTask>()
                    .eq(EtlTask::getFileType, "financial").eq(EtlTask::getCandidateId, task.getCandidateId())
                    .eq(EtlTask::getSourceSha256, task.getSourceSha256()).ne(EtlTask::getId, task.getId())
                    .in(EtlTask::getReviewStatus, "APPROVED", "REJECTED", "PUBLISHED")
                    .orderByDesc(EtlTask::getId).last("LIMIT 1"));
            if (twin != null) {
                task.setReviewStatus(twin.getReviewStatus()); task.setPublishedVersion(twin.getPublishedVersion());
                task.setPublicationReceiptJson(twin.getPublicationReceiptJson()); task.setStockCode(twin.getStockCode());
                task.setCompany(twin.getCompany()); task.setReportYear(twin.getReportYear()); task.setReportPeriod(twin.getReportPeriod());
            }
            }
        }
        try {
            Object steps = Map.of(
                    "steps", report.getOrDefault("steps", List.of()),
                    "retryable_steps", report.getOrDefault("retryable_steps", List.of()));
            task.setStepsJson(objectMapper.writeValueAsString("financial".equals(task.getFileType())
                    ? FinancialSubmissionData.sanitize(steps) : steps));
        } catch (Exception e) {
            throw new IllegalStateException("Cannot serialize ETL result; result was not persisted", e);
        }
        task.setRetryable(false);
        task.setFinishedAt(LocalDateTime.now());
        persistAttemptResult(task);
    }

    private static String nullableString(Object value) {
        return value instanceof String text && !text.isBlank() ? text : null;
    }

    @SuppressWarnings("unchecked")
    private void acceptPersistedReview(EtlTask task, Map<String,Object> report, boolean completed) {
        String status = nullableString(report.get("review_status"));
        Map<?,?> review = report.get("review") instanceof Map<?,?> values ? values : Map.of();
        boolean trustedOperator = review.get("operator_id") instanceof Number operator && operator.longValue() > 0;
        if (completed && "APPROVED".equals(status) && trustedOperator && "approve".equals(review.get("decision"))) {
            task.setReviewStatus("APPROVED");
        } else if ("REJECTED".equals(status) && trustedOperator && "reject".equals(review.get("decision"))
                && nullableString(review.get("reason")) != null) {
            task.setReviewStatus("REJECTED");
        } else if (completed && "PUBLISHED".equals(status)) {
            if (!(report.get("publication") instanceof Map<?,?> publication)) {
                throw new IllegalStateException("Published financial candidate has no receipt");
            }
            Map<String,Object> receipt = FinancialSubmissionData.publicationReceipt((Map<String,Object>) publication);
            if (!receipt.get("version").equals(report.get("published_version"))) {
                throw new IllegalStateException("Published financial candidate receipt does not match its version");
            }
            try {
                task.setPublicationReceiptJson(objectMapper.writeValueAsString(Map.of("receipt", receipt,
                        "candidate_ids", List.of(task.getCandidateId()))));
            } catch (Exception e) { throw new IllegalStateException("Cannot save candidate publication receipt", e); }
            task.setReviewStatus("PUBLISHED"); task.setPublishedVersion((String) receipt.get("version"));
        }
    }

    public void fail(EtlTask task, String message) {
        task.setStatus(STATUS_FAILED);
        task.setMessage(taskMessage(task, message == null ? "Execution failed" : message));
        task.setRetryable(true);
        task.setFinishedAt(LocalDateTime.now());
        persistAttemptResult(task);
    }

    public void markUncertain(EtlTask task, String message) {
        task.setStatus(STATUS_RUNNING);
        task.setMessage(taskMessage(task, message));
        task.setRetryable(false);
        persistAttemptResult(task);
    }

    private void persistAttemptResult(EtlTask task) {
        if (task.getRunToken() == null) {
            throw new IllegalStateException("ETL result has no attempt token");
        }
        requireWrite(taskMapper.update(task, new LambdaUpdateWrapper<EtlTask>()
                .eq(EtlTask::getId, task.getId()).eq(EtlTask::getStatus, STATUS_RUNNING)
                .eq(EtlTask::getRunToken, task.getRunToken())), "persist current ETL attempt");
    }

    private static void requireWrite(int count, String action) {
        if (count != 1) throw new IllegalStateException("Failed to " + action + "; task changed or was not saved");
    }

    private static String taskMessage(EtlTask task, String message) {
        if (!"financial".equals(task.getFileType())) return message;
        String safe = FinancialSubmissionData.text(message);
        return safe == null ? "" : safe.substring(0, Math.min(safe.length(), 2000));
    }

    @Transactional
    public EtlTask retry(long taskId) {
        EtlTask task = taskMapper.selectById(taskId);
        if (task == null) {
            throw new BizException(ErrorCode.NOT_FOUND, "ETL task not found");
        }
        if (!Set.of(STATUS_FAILED, STATUS_PARTIAL).contains(task.getStatus())) {
            throw new BizException(ErrorCode.BAD_REQUEST, "Only failed or partially completed tasks can be retried");
        }
        if ("financial".equals(task.getFileType()) && (
                Set.of("APPROVED", "REJECTED", "PUBLISHED").contains(java.util.Objects.toString(task.getReviewStatus(), ""))
                || !FinancialSubmissionData.recoverable(task, objectMapper))) {
            throw new BizException(ErrorCode.BAD_REQUEST, "Only recoverable processing failures can be retried");
        }

        requireWrite(taskMapper.prepareRetry(taskId), "claim task for retry");
        task.setStatus(STATUS_PENDING);
        task.setMessage("");
        task.setStartedAt(null);
        task.setFinishedAt(null);
        task.setRunToken(null);
        task.setRetryable(false);
        if ("financial".equals(task.getFileType())) task.setReviewStatus("PROCESSING");

        enqueue(task);
        log.info("[etl] task requeued taskId={}", task.getId());
        return task;
    }

    public int recoverStaleRunningTasks(Duration timeout, int limit) {
        LocalDateTime threshold = LocalDateTime.now().minus(timeout);
        List<EtlTask> tasks = taskMapper.selectList(new LambdaQueryWrapper<EtlTask>()
                .eq(EtlTask::getStatus, STATUS_RUNNING)
                .lt(EtlTask::getStartedAt, threshold)
                .orderByAsc(EtlTask::getStartedAt)
                .last("LIMIT " + Math.max(limit, 1)));
        int recovered = 0;
        for (EtlTask task : tasks) {
            task.setStatus(STATUS_FAILED);
            task.setMessage("Task timed out in RUNNING state; retry from admin console or recovery job");
            task.setFinishedAt(LocalDateTime.now());
            task.setRetryable(false);
            recovered += taskMapper.update(task, new LambdaUpdateWrapper<EtlTask>()
                    .eq(EtlTask::getId, task.getId()).eq(EtlTask::getStatus, STATUS_RUNNING)
                    .lt(EtlTask::getStartedAt, threshold)
                    .eq(task.getRunToken() != null, EtlTask::getRunToken, task.getRunToken())
                    .isNull(task.getRunToken() == null, EtlTask::getRunToken));
        }
        return recovered;
    }

    public EtlTask getById(long taskId) {
        return taskMapper.selectById(taskId);
    }

    public Page<EtlTask> page(int page, int size, String status) {
        LambdaQueryWrapper<EtlTask> wrapper = new LambdaQueryWrapper<EtlTask>()
                .orderByDesc(EtlTask::getId);
        if (status != null && !status.isBlank()) {
            wrapper.eq(EtlTask::getStatus, status);
        }
        return taskMapper.selectPage(Page.of(page, size), wrapper);
    }

    public Map<String, Long> statusStats() {
        return Map.of(
                "pending", taskMapper.selectCount(new LambdaQueryWrapper<EtlTask>().eq(EtlTask::getStatus, STATUS_PENDING)),
                "running", taskMapper.selectCount(new LambdaQueryWrapper<EtlTask>().eq(EtlTask::getStatus, STATUS_RUNNING)),
                "success", taskMapper.selectCount(new LambdaQueryWrapper<EtlTask>().eq(EtlTask::getStatus, STATUS_SUCCESS)),
                "partial", taskMapper.selectCount(new LambdaQueryWrapper<EtlTask>().eq(EtlTask::getStatus, STATUS_PARTIAL)),
                "failed", taskMapper.selectCount(new LambdaQueryWrapper<EtlTask>().eq(EtlTask::getStatus, STATUS_FAILED)));
    }
}
