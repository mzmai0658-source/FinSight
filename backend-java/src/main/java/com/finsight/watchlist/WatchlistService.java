package com.finsight.watchlist;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.finsight.chat.AgentClient;
import com.finsight.common.api.ErrorCode;
import com.finsight.common.exception.BizException;
import com.finsight.market.dto.MarketDtos.MarketSnapshot;
import com.finsight.watchlist.entity.Watchlist;
import com.finsight.watchlist.mapper.WatchlistMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Followed codes are user data; company names and metrics follow the selected release. */
@Service
@RequiredArgsConstructor
public class WatchlistService {
    private final WatchlistMapper watchlistMapper;
    private final AgentClient agentClient;

    private MarketSnapshot snapshot() {
        try { return agentClient.marketSnapshot(); }
        catch (Exception e) { throw new BizException(ErrorCode.AGENT_UNAVAILABLE,"当前财报目录暂不可用，请稍后重试"); }
    }

    public void requireAvailable(String code) {
        if (code == null || !code.matches("\\d{6}")) throw new BizException(ErrorCode.BAD_REQUEST,"股票代码格式无效");
        if (!snapshot().details().containsKey(code)) throw new BizException(ErrorCode.NOT_FOUND,"当前财报库未收录该公司");
    }

    public List<Map<String,Object>> list(long userId) {
        var followed=watchlistMapper.selectList(new LambdaQueryWrapper<Watchlist>()
                .eq(Watchlist::getUserId,userId).orderByDesc(Watchlist::getId));
        if (followed.isEmpty()) return List.of();
        var current=snapshot();
        List<Map<String,Object>> result=new ArrayList<>();
        for (var item:followed) {
            var detail=current.details().get(item.getStockCode());
            var core=detail == null ? Map.<String,Object>of() : detail.latestCore();
            Map<String,Object> row=new LinkedHashMap<>();
            row.put("stockCode",item.getStockCode());
            row.put("abbr",detail == null ? item.getStockCode() : detail.profile().abbr());
            row.put("industry",detail == null ? "" : detail.profile().industry());
            row.put("dataStatus",detail == null ? "unavailable" : detail.profile().dataStatus());
            row.put("lastYear",core.get("year"));
            row.put("revenue",core.get("revenue"));
            row.put("netProfit",core.get("net_profit"));
            row.put("netProfitYoy",core.get("net_profit_yoy"));
            row.put("roe",core.get("roe"));
            row.put("createdAt",item.getCreatedAt());
            row.put("experimental",detail != null && detail.dataset() != null && detail.dataset().experimental());
            result.add(row);
        }
        return result;
    }
}
