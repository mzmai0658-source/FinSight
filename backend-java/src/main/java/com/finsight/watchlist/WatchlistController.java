package com.finsight.watchlist;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.finsight.common.api.ApiResponse;
import com.finsight.common.api.ErrorCode;
import com.finsight.common.exception.BizException;
import com.finsight.common.security.SecurityUtils;
import com.finsight.watchlist.entity.Watchlist;
import com.finsight.watchlist.mapper.WatchlistMapper;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

@Tag(name = "自选股", description = "关注/取消关注公司")
@RestController
@RequestMapping("/api/watchlist")
@RequiredArgsConstructor
public class WatchlistController {

    private static final int MAX_WATCH = 50;

    private final WatchlistMapper watchlistMapper;

    @Operation(summary = "自选列表（含最新年报指标）")
    @GetMapping
    public ApiResponse<List<Map<String, Object>>> list() {
        return ApiResponse.ok(watchlistMapper.listWithCompany(SecurityUtils.currentUserId()));
    }

    @Operation(summary = "当前用户已关注的代码集合")
    @GetMapping("/codes")
    public ApiResponse<List<String>> codes() {
        return ApiResponse.ok(watchlistMapper.selectList(
                        new LambdaQueryWrapper<Watchlist>().eq(Watchlist::getUserId, SecurityUtils.currentUserId()))
                .stream().map(Watchlist::getStockCode).toList());
    }

    @Operation(summary = "关注")
    @PostMapping("/{stockCode}")
    public ApiResponse<Void> add(@PathVariable String stockCode) {
        long userId = SecurityUtils.currentUserId();
        long count = watchlistMapper.selectCount(new LambdaQueryWrapper<Watchlist>().eq(Watchlist::getUserId, userId));
        if (count >= MAX_WATCH) {
            throw new BizException(ErrorCode.BAD_REQUEST, "自选股数量已达上限 " + MAX_WATCH);
        }
        boolean exists = watchlistMapper.selectCount(new LambdaQueryWrapper<Watchlist>()
                .eq(Watchlist::getUserId, userId)
                .eq(Watchlist::getStockCode, stockCode)) > 0;
        if (!exists) {
            Watchlist item = new Watchlist();
            item.setUserId(userId);
            item.setStockCode(stockCode);
            watchlistMapper.insert(item);
        }
        return ApiResponse.ok(null);
    }

    @Operation(summary = "取消关注")
    @DeleteMapping("/{stockCode}")
    public ApiResponse<Void> remove(@PathVariable String stockCode) {
        watchlistMapper.delete(new LambdaQueryWrapper<Watchlist>()
                .eq(Watchlist::getUserId, SecurityUtils.currentUserId())
                .eq(Watchlist::getStockCode, stockCode));
        return ApiResponse.ok(null);
    }
}
