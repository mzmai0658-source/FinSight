package com.finsight.market;

import com.finsight.common.api.ApiResponse;
import com.finsight.market.dto.MarketDtos.CompanyDetail;
import com.finsight.market.dto.MarketDtos.CompanyItem;
import com.finsight.market.dto.MarketDtos.CompanySeries;
import com.finsight.market.dto.MarketDtos.HotCompany;
import com.finsight.market.dto.MarketDtos.MarketSummary;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.servlet.http.HttpServletRequest;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/** 作品说明：行情门户（公开只读，无需登录） */
@Tag(name = "market", description = "公司行情与门户大屏")
@RestController
@RequestMapping("/api/market")
@RequiredArgsConstructor
public class MarketController {

    private final MarketService marketService;

    @Operation(summary = "门户大屏聚合")
    @GetMapping("/summary")
    public ApiResponse<MarketSummary> summary() {
        return ApiResponse.ok(marketService.summary());
    }

    @Operation(summary = "公司列表（支持关键词/行业/数据状态过滤）")
    @GetMapping("/companies")
    public ApiResponse<List<CompanyItem>> companies(
            @RequestParam(required = false) String keyword,
            @RequestParam(required = false) String industry,
            @RequestParam(required = false) String status) {
        return ApiResponse.ok(marketService.listCompanies(keyword, industry, status));
    }

    @Operation(summary = "公司详情（计入热度榜与 UV）")
    @GetMapping("/companies/{stockCode}")
    public ApiResponse<CompanyDetail> companyDetail(@PathVariable String stockCode,
                                                    HttpServletRequest request) {
        CompanyDetail detail = marketService.companyDetail(stockCode);
        marketService.recordView(stockCode, visitorKey(request));
        return ApiResponse.ok(detail);
    }

    @Operation(summary = "公司年度指标序列（图表用）")
    @GetMapping("/companies/{stockCode}/series")
    public ApiResponse<CompanySeries> companySeries(@PathVariable String stockCode) {
        return ApiResponse.ok(marketService.companySeries(stockCode));
    }

    @Operation(summary = "公司热度榜（日榜优先，无数据回退总榜）")
    @GetMapping("/hot")
    public ApiResponse<List<HotCompany>> hot(@RequestParam(defaultValue = "10") int limit) {
        return ApiResponse.ok(marketService.hotCompanies(Math.min(Math.max(limit, 1), 50)));
    }

    private String visitorKey(HttpServletRequest request) {
        String forwarded = request.getHeader("X-Forwarded-For");
        String ip = forwarded != null && !forwarded.isBlank()
                ? forwarded.split(",")[0].trim()
                : request.getRemoteAddr();
        String ua = request.getHeader("User-Agent");
        return ip + "|" + (ua == null ? "" : ua.hashCode());
    }
}
