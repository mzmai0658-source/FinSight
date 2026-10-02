package com.finsight.advisor;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * 作品说明：规则评分引擎：四维度各 25 分（成长性 / 盈利质量 / 偿债能力 / 现金流）。
 * 纯函数、无外部依赖，便于单测；数据缺口按维度给保底分并标注。
 */
public final class AdvisorScorer {

    public record Dimension(String name, int score, int max, String comment) {
    }

    public record ScoreResult(int total, String rating, List<Dimension> dimensions) {
    }

    private AdvisorScorer() {
    }

    /**
     * 作品说明：参数及返回值说明。
     * @param latest 最新年报核心指标行（FinanceMapper.coreSeries 末行）
     * @param latestBalance 最新年报资产负债行（可空）
     * @param latestCashflow 最新年报现金流行（可空）
     */
    public static ScoreResult score(Map<String, Object> latest,
                                    Map<String, Object> latestBalance,
                                    Map<String, Object> latestCashflow) {
        List<Dimension> dimensions = new ArrayList<>();
        dimensions.add(growth(latest));
        dimensions.add(profitability(latest));
        dimensions.add(solvency(latestBalance));
        dimensions.add(cashflow(latestCashflow, latest));

        int total = dimensions.stream().mapToInt(Dimension::score).sum();
        return new ScoreResult(total, rating(total), dimensions);
    }

    private static Dimension growth(Map<String, Object> latest) {
        Double revenueYoy = number(latest, "revenue_yoy");
        Double profitYoy = number(latest, "net_profit_yoy");
        if (revenueYoy == null && profitYoy == null) {
            return new Dimension("成长性", 10, 25, "营收/净利同比数据缺口，给中性保底分");
        }
        int score = scaleGrowth(revenueYoy) + scaleGrowth(profitYoy);
        String comment = String.format("营收同比 %s，净利润同比 %s",
                pct(revenueYoy), pct(profitYoy));
        return new Dimension("成长性", score, 25, comment);
    }

    /** 作品说明：单项 0~12.5 → 取整后两项合计最高 25 */
    private static int scaleGrowth(Double yoy) {
        if (yoy == null) {
            return 5;
        }
        if (yoy >= 30) return 12;
        if (yoy >= 15) return 11;
        if (yoy >= 5) return 9;
        if (yoy >= 0) return 7;
        if (yoy >= -15) return 4;
        return 1;
    }

    private static Dimension profitability(Map<String, Object> latest) {
        Double roe = number(latest, "roe");
        Double grossMargin = number(latest, "gross_profit_margin");
        Double netMargin = number(latest, "net_profit_margin");
        if (roe == null && grossMargin == null && netMargin == null) {
            return new Dimension("盈利质量", 10, 25, "ROE/利润率数据缺口，给中性保底分");
        }
        int score = 0;
        if (roe != null) {
            score += roe >= 15 ? 10 : roe >= 10 ? 8 : roe >= 5 ? 5 : roe > 0 ? 3 : 0;
        } else {
            score += 4;
        }
        if (grossMargin != null) {
            score += grossMargin >= 40 ? 8 : grossMargin >= 25 ? 6 : grossMargin >= 15 ? 4 : grossMargin > 0 ? 2 : 0;
        } else {
            score += 3;
        }
        if (netMargin != null) {
            score += netMargin >= 20 ? 7 : netMargin >= 10 ? 5 : netMargin >= 5 ? 3 : netMargin > 0 ? 2 : 0;
        } else {
            score += 3;
        }
        String comment = String.format("ROE %s，毛利率 %s，净利率 %s",
                pct(roe), pct(grossMargin), pct(netMargin));
        return new Dimension("盈利质量", Math.min(score, 25), 25, comment);
    }

    private static Dimension solvency(Map<String, Object> balance) {
        Double ratio = number(balance, "asset_liability_ratio");
        if (ratio == null) {
            return new Dimension("偿债能力", 12, 25, "资产负债率数据缺口，给中性保底分");
        }
        int score = ratio <= 30 ? 25 : ratio <= 45 ? 20 : ratio <= 60 ? 14 : ratio <= 75 ? 8 : 3;
        return new Dimension("偿债能力", score, 25, String.format("资产负债率 %s", pct(ratio)));
    }

    private static Dimension cashflow(Map<String, Object> cashflow, Map<String, Object> latest) {
        Double operatingCf = number(cashflow, "operating_cf");
        Double netProfit = number(latest, "net_profit");
        if (operatingCf == null || netProfit == null || Math.abs(netProfit) < 0.01) {
            return new Dimension("现金流", 12, 25, "经营现金流/净利润数据缺口，给中性保底分");
        }
        if (netProfit < 0) {
            int score = operatingCf > 0 ? 10 : 2;
            return new Dimension("现金流", score, 25,
                    String.format("净利润为负，经营现金流 %s 万元", amount(operatingCf)));
        }
        double coverage = operatingCf / netProfit;
        int score = coverage >= 1.2 ? 25 : coverage >= 0.8 ? 19 : coverage >= 0.5 ? 12 : coverage > 0 ? 6 : 2;
        return new Dimension("现金流", score, 25,
                String.format("经营现金流/净利润 = %.2f（净现比）", coverage));
    }

    public static String rating(int total) {
        if (total >= 80) return "优秀";
        if (total >= 65) return "良好";
        if (total >= 50) return "中性";
        if (total >= 35) return "谨慎";
        return "高风险";
    }

    private static Double number(Map<String, Object> row, String key) {
        if (row == null) {
            return null;
        }
        Object value = row.get(key);
        if (value instanceof BigDecimal decimal) {
            return decimal.doubleValue();
        }
        if (value instanceof Number n) {
            return n.doubleValue();
        }
        return null;
    }

    private static String pct(Double value) {
        return value == null ? "数据缺口" : String.format("%.2f%%", value);
    }

    private static String amount(Double value) {
        return value == null ? "-" : String.format("%.0f", value);
    }
}
