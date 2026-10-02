package com.finsight.report;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.finsight.common.api.ApiResponse;
import com.finsight.report.entity.ResearchReport;
import com.finsight.report.mapper.ResearchReportMapper;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

/** 作品说明：研报库：公开只读（与行情门户一致），数据来自研报元数据 xlsx 入库 */
@Tag(name = "研报库", description = "研究报告元数据检索")
@RestController
@RequestMapping("/api/reports")
@RequiredArgsConstructor
public class ReportController {

    private final ResearchReportMapper reportMapper;

    @Operation(summary = "研报详情")
    @GetMapping("/{id}")
    public ApiResponse<ResearchReport> detail(@PathVariable Long id) {
        ResearchReport report = reportMapper.selectById(id);
        if (report == null) {
            return ApiResponse.error(404, "研报不存在");
        }
        return ApiResponse.ok(report);
    }

    @Operation(summary = "研报分页检索（标题/机构关键词、股票代码、类型过滤）")
    @GetMapping
    public ApiResponse<Map<String, Object>> list(@RequestParam(defaultValue = "1") int page,
                                                 @RequestParam(defaultValue = "10") int size,
                                                 @RequestParam(required = false) String keyword,
                                                 @RequestParam(required = false) String stockCode,
                                                 @RequestParam(required = false) String reportType) {
        LambdaQueryWrapper<ResearchReport> wrapper = new LambdaQueryWrapper<ResearchReport>()
                .orderByDesc(ResearchReport::getPublishDate)
                .orderByDesc(ResearchReport::getId);
        if (keyword != null && !keyword.isBlank()) {
            String kw = keyword.trim();
            wrapper.and(w -> w.like(ResearchReport::getTitle, kw)
                    .or().like(ResearchReport::getOrgSname, kw)
                    .or().like(ResearchReport::getStockName, kw));
        }
        if (stockCode != null && !stockCode.isBlank()) {
            wrapper.eq(ResearchReport::getStockCode, stockCode.trim());
        }
        if (reportType != null && !reportType.isBlank()) {
            wrapper.eq(ResearchReport::getReportType, reportType.trim());
        }

        Page<ResearchReport> result = reportMapper.selectPage(Page.of(page, Math.min(size, 50)), wrapper);
        return ApiResponse.ok(Map.of(
                "total", result.getTotal(),
                "page", page,
                "records", result.getRecords()));
    }

    @Operation(summary = "机构观点卡：某公司最新研报（评级 + 预测）")
    @GetMapping("/institution-view")
    public ApiResponse<List<ResearchReport>> institutionView(@RequestParam String stockCode,
                                                             @RequestParam(defaultValue = "6") int limit) {
        return ApiResponse.ok(reportMapper.selectList(new LambdaQueryWrapper<ResearchReport>()
                .eq(ResearchReport::getStockCode, stockCode)
                .eq(ResearchReport::getReportType, "stock")
                .orderByDesc(ResearchReport::getPublishDate)
                .last("LIMIT " + Math.min(limit, 20))));
    }
}
