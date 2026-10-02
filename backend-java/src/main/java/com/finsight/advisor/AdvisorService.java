package com.finsight.advisor;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.finsight.advisor.AdvisorScorer.ScoreResult;
import com.finsight.advisor.entity.AdvisorReport;
import com.finsight.advisor.mapper.AdvisorReportMapper;
import com.finsight.advisor.mq.AdvisorReportMessage;
import com.finsight.common.api.ErrorCode;
import com.finsight.common.exception.BizException;
import com.finsight.common.mq.MqOutboxService;
import com.finsight.config.RabbitConfig;
import com.finsight.market.entity.Company;
import com.finsight.market.mapper.CompanyMapper;
import com.finsight.market.mapper.FinanceMapper;
import com.finsight.user.entity.SysUser;
import com.finsight.user.mapper.SysUserMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

@Slf4j
@Service
@RequiredArgsConstructor
public class AdvisorService {

    private final FinanceMapper financeMapper;
    private final CompanyMapper companyMapper;
    private final SysUserMapper userMapper;
    private final AdvisorReportMapper reportMapper;
    private final MqOutboxService mqOutboxService;
    private final ObjectMapper objectMapper;

    public Map<String, Object> diagnose(String stockCode) {
        Company company = companyMapper.selectOne(new LambdaQueryWrapper<Company>()
                .eq(Company::getStockCode, stockCode));
        if (company == null) {
            throw new BizException(ErrorCode.NOT_FOUND, "Company not found");
        }
        if (!"imported".equals(company.getDataStatus())) {
            throw new BizException(ErrorCode.BAD_REQUEST, "Financial data has not been imported");
        }

        List<Map<String, Object>> core = financeMapper.coreSeries(stockCode);
        List<Map<String, Object>> balance = financeMapper.balanceSeries(stockCode);
        List<Map<String, Object>> cashflow = financeMapper.cashflowSeries(stockCode);
        if (core.isEmpty()) {
            throw new BizException(ErrorCode.BAD_REQUEST, "Missing annual core financial metrics");
        }

        ScoreResult result = AdvisorScorer.score(last(core), last(balance), last(cashflow));

        Map<String, Object> payload = new HashMap<>();
        payload.put("stockCode", stockCode);
        payload.put("stockName", company.getAbbr());
        payload.put("industry", company.getIndustry());
        payload.put("latestYear", last(core).get("year"));
        payload.put("total", result.total());
        payload.put("rating", result.rating());
        payload.put("dimensions", result.dimensions());
        return payload;
    }

    public long requestReport(long userId, String stockCode) {
        Map<String, Object> diagnosis = diagnose(stockCode);
        SysUser user = userMapper.selectById(userId);
        String riskProfile = user == null ? "balanced" : user.getRiskProfile();

        AdvisorReport report = new AdvisorReport();
        report.setUserId(userId);
        report.setStockCode(stockCode);
        report.setStockName(String.valueOf(diagnosis.get("stockName")));
        report.setRiskProfile(riskProfile);
        report.setScore((Integer) diagnosis.get("total"));
        report.setRating(String.valueOf(diagnosis.get("rating")));
        try {
            report.setDimensions(objectMapper.writeValueAsString(diagnosis.get("dimensions")));
        } catch (Exception ignored) {
        }
        report.setStatus(AdvisorReport.STATUS_GENERATING);
        reportMapper.insert(report);

        mqOutboxService.publish(RabbitConfig.EXCHANGE, RabbitConfig.ADVISOR_REPORT_RK,
                new AdvisorReportMessage(report.getId()));
        return report.getId();
    }

    public AdvisorReport latestReport(long userId, String stockCode) {
        return reportMapper.selectOne(new LambdaQueryWrapper<AdvisorReport>()
                .eq(AdvisorReport::getUserId, userId)
                .eq(AdvisorReport::getStockCode, stockCode)
                .orderByDesc(AdvisorReport::getId)
                .last("LIMIT 1"));
    }

    public AdvisorReport getReport(long userId, long reportId) {
        AdvisorReport report = reportMapper.selectById(reportId);
        if (report == null || !report.getUserId().equals(userId)) {
            throw new BizException(ErrorCode.NOT_FOUND, "Report not found");
        }
        return report;
    }

    private static Map<String, Object> last(List<Map<String, Object>> rows) {
        return rows.isEmpty() ? null : rows.get(rows.size() - 1);
    }
}
