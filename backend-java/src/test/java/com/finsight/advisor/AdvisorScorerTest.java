package com.finsight.advisor;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.HashMap;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

/** 作品说明：规则评分引擎单测：覆盖满数据、数据缺口保底、亏损公司、评级分档。 */
class AdvisorScorerTest {

    private Map<String, Object> indicators(double revenueYoy, double profitYoy,
                                           double roe, double grossMargin, double netMargin,
                                           double netProfit) {
        Map<String, Object> row = new HashMap<>();
        row.put("revenue_yoy", BigDecimal.valueOf(revenueYoy));
        row.put("net_profit_yoy", BigDecimal.valueOf(profitYoy));
        row.put("roe", BigDecimal.valueOf(roe));
        row.put("gross_profit_margin", BigDecimal.valueOf(grossMargin));
        row.put("net_profit_margin", BigDecimal.valueOf(netMargin));
        row.put("net_profit", BigDecimal.valueOf(netProfit));
        return row;
    }

    @Test
    void strongCompanyScoresExcellent() {
        Map<String, Object> latest = indicators(35, 40, 20, 45, 22, 500000);
        Map<String, Object> balance = Map.of("asset_liability_ratio", BigDecimal.valueOf(25));
        Map<String, Object> cashflow = Map.of("operating_cf", BigDecimal.valueOf(700000));

        AdvisorScorer.ScoreResult result = AdvisorScorer.score(latest, balance, cashflow);

        assertThat(result.dimensions()).hasSize(4);
        assertThat(result.total()).isGreaterThanOrEqualTo(80);
        assertThat(result.rating()).isEqualTo("优秀");
        // 作品说明：四个维度均拿到接近满分
        assertThat(result.dimensions()).allSatisfy(d -> assertThat(d.score()).isGreaterThanOrEqualTo(20));
    }

    @Test
    void missingDataFallsBackToNeutralScores() {
        AdvisorScorer.ScoreResult result = AdvisorScorer.score(Map.of(), null, null);

        assertThat(result.total()).isEqualTo(10 + 10 + 12 + 12);
        assertThat(result.rating()).isEqualTo("谨慎");
        assertThat(result.dimensions())
                .allSatisfy(d -> assertThat(d.comment()).contains("缺口"));
    }

    @Test
    void lossMakingCompanyWithPositiveCashflowGetsPartialCredit() {
        Map<String, Object> latest = indicators(-20, -50, -5, 10, -8, -100000);
        Map<String, Object> cashflow = Map.of("operating_cf", BigDecimal.valueOf(50000));

        AdvisorScorer.ScoreResult result = AdvisorScorer.score(latest, null, cashflow);

        AdvisorScorer.Dimension cf = result.dimensions().stream()
                .filter(d -> d.name().equals("现金流")).findFirst().orElseThrow();
        assertThat(cf.score()).isEqualTo(10);
        assertThat(cf.comment()).contains("净利润为负");
        assertThat(result.total()).isLessThan(50);
    }

    @Test
    void cashflowCoverageDrivesScore() {
        Map<String, Object> latest = indicators(10, 10, 12, 30, 12, 100000);
        Map<String, Object> goodCf = Map.of("operating_cf", BigDecimal.valueOf(130000));
        Map<String, Object> weakCf = Map.of("operating_cf", BigDecimal.valueOf(30000));

        int goodScore = AdvisorScorer.score(latest, null, goodCf).dimensions().stream()
                .filter(d -> d.name().equals("现金流")).findFirst().orElseThrow().score();
        int weakScore = AdvisorScorer.score(latest, null, weakCf).dimensions().stream()
                .filter(d -> d.name().equals("现金流")).findFirst().orElseThrow().score();

        assertThat(goodScore).isEqualTo(25); // 作品说明：净现比 1.3 ≥ 1.2
        assertThat(weakScore).isLessThan(goodScore);
    }

    @Test
    void ratingBoundaries() {
        assertThat(AdvisorScorer.rating(80)).isEqualTo("优秀");
        assertThat(AdvisorScorer.rating(79)).isEqualTo("良好");
        assertThat(AdvisorScorer.rating(65)).isEqualTo("良好");
        assertThat(AdvisorScorer.rating(64)).isEqualTo("中性");
        assertThat(AdvisorScorer.rating(50)).isEqualTo("中性");
        assertThat(AdvisorScorer.rating(49)).isEqualTo("谨慎");
        assertThat(AdvisorScorer.rating(34)).isEqualTo("高风险");
    }

    @Test
    void totalNeverExceedsHundred() {
        Map<String, Object> latest = indicators(100, 100, 50, 90, 60, 1000000);
        Map<String, Object> balance = Map.of("asset_liability_ratio", BigDecimal.valueOf(5));
        Map<String, Object> cashflow = Map.of("operating_cf", BigDecimal.valueOf(5000000));

        AdvisorScorer.ScoreResult result = AdvisorScorer.score(latest, balance, cashflow);

        assertThat(result.total()).isLessThanOrEqualTo(100);
        assertThat(result.dimensions()).allSatisfy(d -> assertThat(d.score()).isLessThanOrEqualTo(d.max()));
    }
}
