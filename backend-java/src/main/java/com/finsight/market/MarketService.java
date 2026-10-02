package com.finsight.market;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.finsight.common.api.ErrorCode;
import com.finsight.common.exception.BizException;
import com.finsight.common.metrics.ObservabilityService;
import com.finsight.market.dto.MarketDtos.CompanyDetail;
import com.finsight.market.dto.MarketDtos.CompanyItem;
import com.finsight.market.dto.MarketDtos.CompanySeries;
import com.finsight.market.dto.MarketDtos.HotCompany;
import com.finsight.market.dto.MarketDtos.MarketSummary;
import com.finsight.market.entity.Company;
import com.finsight.market.mapper.CompanyMapper;
import com.finsight.market.mapper.FinanceMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ZSetOperations;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ThreadLocalRandom;
import java.util.function.Supplier;

/**
 * 作品说明：行情/公司模块，Redis 缓存专项：
 * - Cache-Aside + TTL 随机抖动（防雪崩）
 * - 未知公司缓存空值短 TTL（防穿透）
 * - 大屏聚合用 setnx 互斥重建（防击穿）
 * - ZSet 公司热度榜（日榜 + 总榜），HyperLogLog 访客 UV
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class MarketService {

    private static final String KEY_COMPANIES = "market:companies";
    private static final String KEY_DETAIL = "market:detail:";
    private static final String KEY_SERIES = "market:series:";
    private static final String KEY_SUMMARY = "market:summary";
    private static final String KEY_SUMMARY_LOCK = "market:summary:lock";
    private static final String KEY_HOT_TOTAL = "market:hot:total";
    private static final String KEY_HOT_DAY_PREFIX = "market:hot:";
    private static final String KEY_UV_PREFIX = "market:uv:";
    private static final String NULL_SENTINEL = "__NULL__";

    private static final DateTimeFormatter DAY_FMT = DateTimeFormatter.ofPattern("yyyyMMdd");

    private final CompanyMapper companyMapper;
    private final FinanceMapper financeMapper;
    private final StringRedisTemplate redis;
    private final ObjectMapper objectMapper;
    private final ObservabilityService observabilityService;

    /* 作品说明：公司列表 */

    public List<CompanyItem> listCompanies(String keyword, String industry, String status) {
        List<CompanyItem> all = cached(KEY_COMPANIES, Duration.ofMinutes(10),
                new TypeReference<>() {
                }, this::loadAllCompanies);
        return all.stream()
                .filter(c -> keyword == null || keyword.isBlank()
                        || c.abbr().contains(keyword) || c.stockCode().contains(keyword)
                        || c.fullName().contains(keyword))
                .filter(c -> industry == null || industry.isBlank() || c.industry().contains(industry))
                .filter(c -> status == null || status.isBlank() || c.dataStatus().equals(status))
                .toList();
    }

    private List<CompanyItem> loadAllCompanies() {
        return companyMapper.selectList(new LambdaQueryWrapper<Company>()
                        .orderByDesc(Company::getDataStatus)
                        .orderByAsc(Company::getStockCode))
                .stream().map(MarketService::toItem).toList();
    }

    /* 作品说明：公司详情 */

    public CompanyDetail companyDetail(String stockCode) {
        String key = KEY_DETAIL + stockCode;
        String raw = redis.opsForValue().get(key);
        if (NULL_SENTINEL.equals(raw)) {
            observabilityService.recordMarketCacheHit(cacheLabel(key));
            throw new BizException(ErrorCode.NOT_FOUND, "未收录该公司");
        }
        if (raw != null) {
            observabilityService.recordMarketCacheHit(cacheLabel(key));
            return readJson(raw, new TypeReference<>() {
            });
        }
        observabilityService.recordMarketCacheMiss(cacheLabel(key));
        Company company = companyMapper.selectOne(
                new LambdaQueryWrapper<Company>().eq(Company::getStockCode, stockCode));
        if (company == null) {
            // 作品说明：防穿透：未知代码缓存空哨兵 60s
            redis.opsForValue().set(key, NULL_SENTINEL, Duration.ofSeconds(60));
            throw new BizException(ErrorCode.NOT_FOUND, "未收录该公司");
        }
        List<Map<String, Object>> coreSeries = financeMapper.coreSeries(stockCode);
        Map<String, Object> latestCore = coreSeries.isEmpty()
                ? Map.of()
                : coreSeries.get(coreSeries.size() - 1);
        CompanyDetail detail = new CompanyDetail(
                toItem(company),
                company.getEnName(),
                company.getRegCapital(),
                company.getEmployees(),
                latestCore,
                financeMapper.coverage(stockCode));
        redis.opsForValue().set(key, writeJson(detail), withJitter(Duration.ofMinutes(10)));
        return detail;
    }

    /* 作品说明：图表序列 */

    public CompanySeries companySeries(String stockCode) {
        return cached(KEY_SERIES + stockCode, Duration.ofMinutes(30),
                new TypeReference<>() {
                },
                () -> new CompanySeries(
                        stockCode,
                        financeMapper.coreSeries(stockCode),
                        financeMapper.balanceSeries(stockCode),
                        financeMapper.cashflowSeries(stockCode)));
    }

    /* 作品说明：门户大屏聚合 */

    public MarketSummary summary() {
        String raw = redis.opsForValue().get(KEY_SUMMARY);
        if (raw != null) {
            observabilityService.recordMarketCacheHit(cacheLabel(KEY_SUMMARY));
            return readJson(raw, new TypeReference<>() {
            });
        }
        observabilityService.recordMarketCacheMiss(cacheLabel(KEY_SUMMARY));
        // 作品说明：防击穿：互斥重建，抢锁失败时直接回源（本查询成本可控，避免自旋等待）
        Boolean locked = redis.opsForValue().setIfAbsent(KEY_SUMMARY_LOCK, "1", Duration.ofSeconds(10));
        MarketSummary summary = buildSummary();
        if (Boolean.TRUE.equals(locked)) {
            try {
                redis.opsForValue().set(KEY_SUMMARY, writeJson(summary), withJitter(Duration.ofMinutes(5)));
            } finally {
                redis.delete(KEY_SUMMARY_LOCK);
            }
        }
        return summary;
    }

    private MarketSummary buildSummary() {
        long imported = companyMapper.selectCount(
                new LambdaQueryWrapper<Company>().eq(Company::getDataStatus, "imported"));
        long pending = companyMapper.selectCount(
                new LambdaQueryWrapper<Company>().eq(Company::getDataStatus, "pending"));
        Integer latestYear = financeMapper.latestFiscalYear();
        List<Map<String, Object>> rank = latestYear == null
                ? List.of()
                : financeMapper.companyRankByYear(latestYear);
        return new MarketSummary(
                imported,
                pending,
                latestYear,
                industryDistribution(),
                financeMapper.yearlyAggregate(),
                rank,
                hotCompanies(5));
    }

    private List<Map<String, Object>> industryDistribution() {
        Map<String, long[]> grouped = new LinkedHashMap<>();
        for (CompanyItem item : listCompanies(null, null, null)) {
            // 作品说明：行业保留完整分类名称，避免过度合并后丢失细分信息。
            long[] counter = grouped.computeIfAbsent(item.industry(), k -> new long[2]);
            counter[0]++;
            if ("imported".equals(item.dataStatus())) {
                counter[1]++;
            }
        }
        List<Map<String, Object>> result = new ArrayList<>();
        grouped.entrySet().stream()
                .sorted(Comparator.comparingLong((Map.Entry<String, long[]> e) -> e.getValue()[0]).reversed())
                .forEach(e -> {
                    Map<String, Object> row = new HashMap<>();
                    row.put("industry", e.getKey());
                    row.put("total", e.getValue()[0]);
                    row.put("imported", e.getValue()[1]);
                    result.add(row);
                });
        return result;
    }

    /* 作品说明：热度榜 + UV */

    public void recordView(String stockCode, String visitorKey) {
        try {
            String day = LocalDate.now().format(DAY_FMT);
            String dayKey = KEY_HOT_DAY_PREFIX + day;
            redis.opsForZSet().incrementScore(dayKey, stockCode, 1);
            redis.expire(dayKey, Duration.ofDays(8));
            redis.opsForZSet().incrementScore(KEY_HOT_TOTAL, stockCode, 1);
            String uvKey = KEY_UV_PREFIX + day;
            redis.opsForHyperLogLog().add(uvKey, visitorKey);
            redis.expire(uvKey, Duration.ofDays(35));
        } catch (Exception e) {
            log.warn("[market] 热度统计失败（不影响主流程）: {}", e.toString());
        }
    }

    public List<HotCompany> hotCompanies(int limit) {
        String dayKey = KEY_HOT_DAY_PREFIX + LocalDate.now().format(DAY_FMT);
        Set<ZSetOperations.TypedTuple<String>> tuples =
                redis.opsForZSet().reverseRangeWithScores(dayKey, 0, limit - 1L);
        if (tuples == null || tuples.isEmpty()) {
            tuples = redis.opsForZSet().reverseRangeWithScores(KEY_HOT_TOTAL, 0, limit - 1L);
        }
        if (tuples == null || tuples.isEmpty()) {
            return List.of();
        }
        Map<String, CompanyItem> byCode = new HashMap<>();
        for (CompanyItem item : listCompanies(null, null, null)) {
            byCode.put(item.stockCode(), item);
        }
        List<HotCompany> result = new ArrayList<>();
        for (ZSetOperations.TypedTuple<String> tuple : tuples) {
            String code = tuple.getValue();
            CompanyItem item = code == null ? null : byCode.get(code);
            if (item == null) {
                continue;
            }
            long views = tuple.getScore() == null ? 0 : tuple.getScore().longValue();
            result.add(new HotCompany(item.stockCode(), item.abbr(), item.industry(), views));
        }
        return result;
    }

    /** 作品说明：今日访客 UV（HyperLogLog 近似计数，管理端看板用） */
    public long todayUv() {
        Long count = redis.opsForHyperLogLog()
                .size(KEY_UV_PREFIX + LocalDate.now().format(DAY_FMT));
        return count == null ? 0 : count;
    }

    /* 作品说明：缓存工具 */

    private <T> T cached(String key, Duration ttl, TypeReference<T> type, Supplier<T> loader) {
        String raw = redis.opsForValue().get(key);
        if (raw != null) {
            observabilityService.recordMarketCacheHit(cacheLabel(key));
            return readJson(raw, type);
        }
        observabilityService.recordMarketCacheMiss(cacheLabel(key));
        T value = loader.get();
        redis.opsForValue().set(key, writeJson(value), withJitter(ttl));
        return value;
    }

    /** 作品说明：TTL 抖动 ±20%：错峰过期防雪崩 */
    private static String cacheLabel(String key) {
        if (key == null) {
            return "unknown";
        }
        if (key.startsWith(KEY_DETAIL)) {
            return "detail";
        }
        if (key.startsWith(KEY_SERIES)) {
            return "series";
        }
        if (key.startsWith(KEY_COMPANIES)) {
            return "companies";
        }
        if (KEY_SUMMARY.equals(key)) {
            return "summary";
        }
        return "other";
    }

    private Duration withJitter(Duration base) {
        long seconds = base.toSeconds();
        long jitter = ThreadLocalRandom.current().nextLong(-seconds / 5, seconds / 5 + 1);
        return Duration.ofSeconds(seconds + jitter);
    }

    private String writeJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (Exception e) {
            throw new IllegalStateException("缓存序列化失败", e);
        }
    }

    private <T> T readJson(String raw, TypeReference<T> type) {
        try {
            return objectMapper.readValue(raw, type);
        } catch (Exception e) {
            throw new IllegalStateException("缓存反序列化失败", e);
        }
    }

    private static CompanyItem toItem(Company c) {
        return new CompanyItem(
                c.getStockCode(), c.getAbbr(), c.getFullName(), c.getExchange(), c.getBoard(),
                c.getIndustry(), c.getRegion(), c.getDatasetTag(), c.getDataStatus(),
                c.getFirstYear(), c.getLastYear());
    }
}
