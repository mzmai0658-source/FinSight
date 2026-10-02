package com.finsight.watchlist.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.finsight.watchlist.entity.Watchlist;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

import java.util.List;
import java.util.Map;

@Mapper
public interface WatchlistMapper extends BaseMapper<Watchlist> {

    /** 作品说明：自选列表联表公司主数据 + 最新年报核心指标 */
    @Select("""
            SELECT w.stock_code                      AS stockCode,
                   c.abbr                            AS abbr,
                   c.industry                        AS industry,
                   c.data_status                     AS dataStatus,
                   c.last_year                       AS lastYear,
                   k.total_operating_revenue         AS revenue,
                   k.net_profit_10k_yuan             AS netProfit,
                   k.net_profit_yoy_growth           AS netProfitYoy,
                   k.roe                             AS roe,
                   w.created_at                      AS createdAt
            FROM watchlist w
            LEFT JOIN company c ON c.stock_code = w.stock_code
            LEFT JOIN core_performance_indicators_sheet k
                   ON k.stock_code = w.stock_code AND k.report_period = 'FY' AND k.report_year = c.last_year
            WHERE w.user_id = #{userId}
            ORDER BY w.id DESC
            """)
    List<Map<String, Object>> listWithCompany(@Param("userId") long userId);
}
