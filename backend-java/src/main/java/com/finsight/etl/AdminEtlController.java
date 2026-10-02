package com.finsight.etl;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.finsight.common.api.ApiResponse;
import com.finsight.common.ratelimit.RateLimit;
import com.finsight.common.ratelimit.RateLimitKey;
import com.finsight.common.security.SecurityUtils;
import com.finsight.etl.entity.EtlTask;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;
import java.util.Map;

/** 作品说明：管理端 ETL：财报/研报上传 + 任务状态机查询（/api/admin/** 已由安全配置限 ADMIN） */
@Tag(name = "管理端-ETL", description = "报告上传与处理任务")
@RestController
@RequestMapping("/api/admin/etl")
@RequiredArgsConstructor
public class AdminEtlController {

    private final EtlTaskService taskService;

    public record TaskView(long id, String taskUid, String fileName, String fileType, String status,
                           String stockCode, Integer reportYear, String message, String stepsJson,
                           String createdAt, String startedAt, String finishedAt) {

        static TaskView from(EtlTask task) {
            return new TaskView(task.getId(), task.getTaskUid(), task.getFileName(), task.getFileType(),
                    task.getStatus(), task.getStockCode(), task.getReportYear(), task.getMessage(),
                    task.getStepsJson(),
                    task.getCreatedAt() == null ? "" : task.getCreatedAt().toString(),
                    task.getStartedAt() == null ? "" : task.getStartedAt().toString(),
                    task.getFinishedAt() == null ? "" : task.getFinishedAt().toString());
        }
    }

    @Operation(summary = "上传报告 PDF 并触发处理管线")
    @PostMapping("/upload")
    @RateLimit(name = "etl-upload", keyBy = RateLimitKey.USER_IP, windowSeconds = 300, limit = 10,
            message = "上传过于频繁，请稍后再试")
    public ApiResponse<TaskView> upload(@RequestParam("file") MultipartFile file,
                                        @RequestParam(value = "fileType", defaultValue = "financial") String fileType) {
        EtlTask task = taskService.submit(file, fileType, SecurityUtils.currentUserId());
        return ApiResponse.ok(TaskView.from(task));
    }

    @Operation(summary = "任务列表（状态机视图）")
    @GetMapping("/tasks")
    public ApiResponse<Map<String, Object>> tasks(@RequestParam(defaultValue = "1") int page,
                                                  @RequestParam(defaultValue = "10") int size,
                                                  @RequestParam(required = false) String status) {
        Page<EtlTask> result = taskService.page(page, Math.min(size, 50), status);
        List<TaskView> records = result.getRecords().stream().map(TaskView::from).toList();
        return ApiResponse.ok(Map.of(
                "total", result.getTotal(),
                "page", page,
                "records", records,
                "stats", taskService.statusStats()));
    }

    @Operation(summary = "重试失败 ETL 任务（补偿入队）")
    @PostMapping("/tasks/{taskId}/retry")
    public ApiResponse<TaskView> retry(@PathVariable long taskId) {
        return ApiResponse.ok(TaskView.from(taskService.retry(taskId)));
    }
}
