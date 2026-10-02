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
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;
import java.time.Duration;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

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
        if (!original.toLowerCase().endsWith(".pdf")) {
            throw new BizException(ErrorCode.BAD_REQUEST, "Only PDF files are supported");
        }
        if (!FILE_TYPES.contains(fileType)) {
            throw new BizException(ErrorCode.BAD_REQUEST, "fileType only supports financial / research");
        }

        String taskUid = UUID.randomUUID().toString();
        Path target = storeFile(file, original, taskUid);

        EtlTask task = new EtlTask();
        task.setTaskUid(taskUid);
        task.setFileName(original);
        task.setFilePath(target.toAbsolutePath().toString());
        task.setFileType(fileType);
        task.setStatus(STATUS_PENDING);
        task.setMessage("");
        task.setStockCode("");
        task.setCreatedBy(userId);
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
            throw new BizException(ErrorCode.INTERNAL_ERROR, "File save failed: " + e.getMessage());
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
        return true;
    }

    public void complete(EtlTask task, Map<String, Object> report) {
        task.setStatus(switch (String.valueOf(report.get("status"))) {
            case "success" -> STATUS_SUCCESS;
            case "partial" -> STATUS_PARTIAL;
            default -> STATUS_FAILED;
        });
        task.setMessage(String.valueOf(report.getOrDefault("message", "")));
        task.setStockCode(String.valueOf(report.getOrDefault("stock_code", "")));
        Object year = report.get("report_year");
        if (year instanceof Number number) {
            task.setReportYear(number.intValue());
        }
        try {
            task.setStepsJson(objectMapper.writeValueAsString(Map.of(
                    "steps", report.getOrDefault("steps", List.of()),
                    "retryable_steps", report.getOrDefault("retryable_steps", List.of()))));
        } catch (Exception e) {
            throw new IllegalStateException("Cannot serialize ETL result; result was not persisted", e);
        }
        task.setRetryable(false);
        task.setFinishedAt(LocalDateTime.now());
        persistAttemptResult(task);
    }

    public void fail(EtlTask task, String message) {
        task.setStatus(STATUS_FAILED);
        task.setMessage(message == null ? "Execution failed" : message);
        task.setRetryable(true);
        task.setFinishedAt(LocalDateTime.now());
        persistAttemptResult(task);
    }

    public void markUncertain(EtlTask task, String message) {
        task.setStatus(STATUS_RUNNING);
        task.setMessage(message);
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

    @Transactional
    public EtlTask retry(long taskId) {
        EtlTask task = taskMapper.selectById(taskId);
        if (task == null) {
            throw new BizException(ErrorCode.NOT_FOUND, "ETL task not found");
        }
        if (!Set.of(STATUS_FAILED, STATUS_PARTIAL).contains(task.getStatus())) {
            throw new BizException(ErrorCode.BAD_REQUEST, "Only failed or partially completed tasks can be retried");
        }

        requireWrite(taskMapper.prepareRetry(taskId), "claim task for retry");
        task.setStatus(STATUS_PENDING);
        task.setMessage("");
        task.setStartedAt(null);
        task.setFinishedAt(null);
        task.setRunToken(null);
        task.setRetryable(false);

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
