package com.finsight.market.mapper;

import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

import java.util.List;
import java.util.Map;

/**
 * 作品说明：财务数据只读查询（ETL 导入的四张财务表）。
 * 仅查 FY（年报）口径用于展示；金额单位：万元。
 */
@Mapper
public interface FinanceMapper {

    @Select("""
            SELECT report_year                       AS year,
                   eps,
                   total_operating_revenue           AS revenue,
                   operating_revenue_yoy_growth      AS revenue_yoy,
                   net_profit_10k_yuan               AS net_profit,
                   net_profit_yoy_growth             AS net_profit_yoy,
                   net_profit_excl_non_recurring     AS net_profit_excl,
                   net_profit_excl_non_recurring_yoy AS net_profit_excl_yoy,
                   roe,
                   gross_profit_margin,
                   net_profit_margin,
                   net_asset_per_share,
                   operating_cf_per_share
            FROM core_performance_indicators_sheet
            WHERE stock_code = #{code} AND report_period = 'FY'
            ORDER BY report_year
            """)
    List<Map<String, Object>> coreSeries(@Param("code") String code);

    @Select("""
            SELECT report_year                    AS year,
                   asset_total_assets             AS total_assets,
                   liability_total_liabilities    AS total_liabilities,
                   asset_liability_ratio,
                   asset_cash_and_cash_equivalents AS cash
            FROM balance_sheet
            WHERE stock_code = #{code} AND report_period = 'FY'
            ORDER BY report_year
            """)
    List<Map<String, Object>> balanceSeries(@Param("code") String code);

    @Select("""
            SELECT report_year             AS year,
                   operating_cf_net_amount AS operating_cf,
                   investing_cf_net_amount AS investing_cf,
                   financing_cf_net_amount AS financing_cf,
                   net_cash_flow
            FROM cash_flow_sheet
            WHERE stock_code = #{code} AND report_period = 'FY'
            ORDER BY report_year
            """)
    List<Map<String, Object>> cashflowSeries(@Param("code") String code);

    @Select("""
            SELECT report_year AS year, GROUP_CONCAT(report_period ORDER BY report_period) AS periods
            FROM core_performance_indicators_sheet
            WHERE stock_code = #{code}
            GROUP BY report_year
            ORDER BY report_year
            """)
    List<Map<String, Object>> coverage(@Param("code") String code);

    @Select("""
            SELECT report_year                    AS year,
                   COUNT(*)                       AS company_count,
                   SUM(total_operating_revenue)   AS revenue_sum,
                   SUM(net_profit_10k_yuan)       AS net_profit_sum,
                   AVG(roe)                       AS roe_avg,
                   AVG(gross_profit_margin)       AS gross_margin_avg
            FROM core_performance_indicators_sheet
            WHERE report_period = 'FY'
            GROUP BY report_year
            ORDER BY report_year
            """)
    List<Map<String, Object>> yearlyAggregate();

    @Select("""
            SELECT c.stock_code AS code, c.abbr,
                   k.total_operating_revenue AS revenue,
                   k.net_profit_10k_yuan     AS net_profit,
                   k.net_profit_yoy_growth   AS net_profit_yoy,
                   k.roe
            FROM core_performance_indicators_sheet k
            JOIN company c
              ON c.stock_code COLLATE utf8mb4_unicode_ci
               = k.stock_code COLLATE utf8mb4_unicode_ci
            WHERE k.report_period = 'FY' AND k.report_year = #{year}
            ORDER BY k.total_operating_revenue DESC
            """)
    List<Map<String, Object>> companyRankByYear(@Param("year") int year);

    @Select("SELECT MAX(report_year) FROM core_performance_indicators_sheet WHERE report_period = 'FY'")
    Integer latestFiscalYear();
}
