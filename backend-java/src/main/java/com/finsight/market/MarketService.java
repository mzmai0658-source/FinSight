package com.finsight.market;

import com.finsight.chat.AgentClient;
import com.finsight.common.api.ErrorCode;
import com.finsight.common.exception.BizException;
import com.finsight.market.dto.MarketDtos.*;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ZSetOperations;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Company data follows the selected canonical release; Redis stores view statistics only. */
@Slf4j
@Service
@RequiredArgsConstructor
public class MarketService {
    private static final String KEY_HOT_TOTAL = "market:hot:total";
    private static final String KEY_HOT_DAY_PREFIX = "market:hot:";
    private static final String KEY_UV_PREFIX = "market:uv:";
    private static final DateTimeFormatter DAY_FMT = DateTimeFormatter.ofPattern("yyyyMMdd");

    private final AgentClient agentClient;
    private final StringRedisTemplate redis;

    private MarketSnapshot snapshot() {
        try {
            return agentClient.marketSnapshot();
        } catch (Exception e) {
            log.warn("[market] Selected catalogue unavailable: {}", e.getClass().getSimpleName());
            throw new BizException(ErrorCode.AGENT_UNAVAILABLE, "当前财报目录暂不可用，请稍后重试");
        }
    }

    public List<CompanyItem> listCompanies(String keyword, String industry, String status) {
        return snapshot().companies().stream()
                .filter(c -> keyword == null || keyword.isBlank()
                        || c.abbr().contains(keyword) || c.stockCode().contains(keyword)
                        || c.fullName().contains(keyword))
                .filter(c -> industry == null || industry.isBlank() || c.industry().contains(industry))
                .filter(c -> status == null || status.isBlank() || c.dataStatus().equals(status))
                .toList();
    }

    public CompanyDetail companyDetail(String stockCode) {
        CompanyDetail detail = snapshot().details().get(stockCode);
        if (detail == null) throw new BizException(ErrorCode.NOT_FOUND, "当前财报库未收录该公司");
        return detail;
    }

    public CompanySeries companySeries(String stockCode) {
        CompanySeries series = snapshot().series().get(stockCode);
        if (series == null) throw new BizException(ErrorCode.NOT_FOUND, "当前财报库未收录该公司");
        return series;
    }

    public MarketSummary summary() {
        MarketSnapshot current = snapshot();
        MarketSummary s = current.summary();
        return new MarketSummary(s.importedCompanies(), s.pendingCompanies(), s.latestYear(),
                s.industryDistribution(), s.yearlyAggregate(), s.companyRank(),
                hotCompanies(5, current.companies()), s.dataset());
    }

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
            log.warn("[market] 热度统计失败（不影响主流程）: {}", e.getClass().getSimpleName());
        }
    }

    public List<HotCompany> hotCompanies(int limit) {
        return hotCompanies(limit, snapshot().companies());
    }

    private List<HotCompany> hotCompanies(int limit, List<CompanyItem> companies) {
        try {
            String dayKey = KEY_HOT_DAY_PREFIX + LocalDate.now().format(DAY_FMT);
            Set<ZSetOperations.TypedTuple<String>> tuples =
                    redis.opsForZSet().reverseRangeWithScores(dayKey, 0, limit - 1L);
            if (tuples == null || tuples.isEmpty())
                tuples = redis.opsForZSet().reverseRangeWithScores(KEY_HOT_TOTAL, 0, limit - 1L);
            if (tuples == null) return List.of();
            Map<String, CompanyItem> byCode = new HashMap<>();
            companies.forEach(c -> byCode.put(c.stockCode(), c));
            List<HotCompany> result = new ArrayList<>();
            for (var tuple : tuples) {
                CompanyItem item = byCode.get(tuple.getValue());
                if (item != null) result.add(new HotCompany(item.stockCode(), item.abbr(), item.industry(),
                        tuple.getScore() == null ? 0 : tuple.getScore().longValue()));
            }
            return result;
        } catch (Exception e) {
            log.warn("[market] 热度榜暂不可读取: {}", e.getClass().getSimpleName());
            return List.of();
        }
    }

    public long todayUv() {
        Long count = redis.opsForHyperLogLog().size(KEY_UV_PREFIX + LocalDate.now().format(DAY_FMT));
        return count == null ? 0 : count;
    }
}
