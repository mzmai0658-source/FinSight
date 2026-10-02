package com.finsight.market.dto;

import java.util.List;
import java.util.Map;

public final class MarketDtos {

    private MarketDtos() {
    }

    /** 作品说明：公司列表项 */
    public record CompanyItem(
            String stockCode,
            String abbr,
            String fullName,
            String exchange,
            String board,
            String industry,
            String region,
            String datasetTag,
            String dataStatus,
            Integer firstYear,
            Integer lastYear) {
    }

    /** 作品说明：公司详情：档案 + 最新年报核心指标 + 数据覆盖 */
    public record CompanyDetail(
            CompanyItem profile,
            String enName,
            String regCapital,
            Integer employees,
            Map<String, Object> latestCore,
            List<Map<String, Object>> coverage) {
    }

    /** 作品说明：图表序列：core/balance/cashflow 三组年度数据 */
    public record CompanySeries(
            String stockCode,
            List<Map<String, Object>> core,
            List<Map<String, Object>> balance,
            List<Map<String, Object>> cashflow) {
    }

    /** 作品说明：热度榜条目 */
    public record HotCompany(String stockCode, String abbr, String industry, long views) {
    }

    /** 作品说明：门户大屏聚合 */
    public record MarketSummary(
            long importedCompanies,
            long pendingCompanies,
            Integer latestYear,
            List<Map<String, Object>> industryDistribution,
            List<Map<String, Object>> yearlyAggregate,
            List<Map<String, Object>> companyRank,
            List<HotCompany> hotCompanies) {
    }
}
