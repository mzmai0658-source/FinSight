package com.finsight.advisor;

import com.finsight.advisor.entity.AdvisorReport;
import com.finsight.common.api.ApiResponse;
import com.finsight.common.ratelimit.RateLimit;
import com.finsight.common.security.SecurityUtils;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.HashMap;
import java.util.Map;

@Tag(name = "AI 诊股", description = "规则评分 + LLM 个性化诊断报告")
@RestController
@RequestMapping("/api/advisor")
@RequiredArgsConstructor
@ConditionalOnProperty(name = "finsight.features.advisor-enabled", havingValue = "true")
public class AdvisorController {

    private final AdvisorService advisorService;

    @Operation(summary = "规则评分 + 当前用户最近一份报告")
    @GetMapping("/{stockCode}")
    public ApiResponse<Map<String, Object>> diagnosis(@PathVariable String stockCode) {
        Map<String, Object> result = new HashMap<>(advisorService.diagnose(stockCode));
        AdvisorReport latest = advisorService.latestReport(SecurityUtils.currentUserId(), stockCode);
        result.put("report", latest);
        return ApiResponse.ok(result);
    }

    @Operation(summary = "触发 LLM 诊断报告（异步，完成后站内信通知）")
    @RateLimit(name = "advisor-report", windowSeconds = 60, limit = 3, message = "报告生成触发过于频繁，请稍后再试")
    @PostMapping("/{stockCode}/report")
    public ApiResponse<Map<String, Object>> generate(@PathVariable String stockCode) {
        long reportId = advisorService.requestReport(SecurityUtils.currentUserId(), stockCode);
        return ApiResponse.ok(Map.of("reportId", reportId, "status", AdvisorReport.STATUS_GENERATING));
    }

    @Operation(summary = "按 id 查询报告（前端轮询用）")
    @GetMapping("/reports/{reportId}")
    public ApiResponse<AdvisorReport> report(@PathVariable long reportId) {
        return ApiResponse.ok(advisorService.getReport(SecurityUtils.currentUserId(), reportId));
    }
}
