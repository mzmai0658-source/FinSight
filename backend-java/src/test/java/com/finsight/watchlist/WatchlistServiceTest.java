package com.finsight.watchlist;

import com.finsight.auth.LoginUser;
import com.finsight.chat.AgentClient;
import com.finsight.common.exception.BizException;
import com.finsight.market.dto.MarketDtos.*;
import com.finsight.watchlist.entity.Watchlist;
import com.finsight.watchlist.mapper.WatchlistMapper;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import java.util.List;
import java.util.Map;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class WatchlistServiceTest {
    private final WatchlistMapper mapper=mock(WatchlistMapper.class);
    private final AgentClient agent=mock(AgentClient.class);
    private final WatchlistService service=new WatchlistService(mapper,agent);
    @AfterEach void clear(){SecurityContextHolder.clearContext();}

    private MarketSnapshot snapshot() {
        var profile=new CompanyItem("300750","宁德时代","","","","","","试验财报库","imported",2023,2026);
        var dataset=new DatasetInfo("trial",true,"试验财报库",119,10,false);
        var detail=new CompanyDetail(profile,"","",null,
            Map.of("year",2025,"revenue","42370183.4","net_profit","7220128.2","roe","24.91"),List.of(),dataset);
        return new MarketSnapshot(List.of(profile),null,Map.of("300750",detail),Map.of(),List.of());
    }

    private Watchlist followed(String code) {
        var item=new Watchlist();item.setStockCode(code);item.setUserId(7L);return item;
    }

    @Test void selectedCompanyNamesAndAnnualMetricsAreUsedInsteadOfLegacyJoins() {
        when(mapper.selectList(any())).thenReturn(List.of(followed("300750"),followed("999999")));
        when(agent.marketSnapshot()).thenReturn(snapshot());
        var rows=service.list(7);
        assertEquals("宁德时代",rows.get(0).get("abbr"));
        assertEquals(2025,rows.get(0).get("lastYear"));
        assertEquals("42370183.4",rows.get(0).get("revenue"));
        assertEquals(true,rows.get(0).get("experimental"));
        assertNull(rows.get(0).get("netProfitYoy"));
        assertEquals("unavailable",rows.get(1).get("dataStatus"));
        assertNull(rows.get(1).get("revenue"));
        verify(agent,times(1)).marketSnapshot();
    }

    @Test void anEmptyListDoesNotDependOnTheFinancialService() {
        when(mapper.selectList(any())).thenReturn(List.of());
        assertTrue(service.list(7).isEmpty());verifyNoInteractions(agent);
    }

    @Test void unknownOrMalformedCodesCannotBeNewlyFollowed() {
        assertThrows(BizException.class,()->service.requireAvailable("../secret"));
        verifyNoInteractions(agent);
        when(agent.marketSnapshot()).thenReturn(snapshot());
        assertThrows(BizException.class,()->service.requireAvailable("999999"));
        assertDoesNotThrow(()->service.requireAvailable("300750"));
    }

    @Test void snapshotFailureIsExplicitAndDoesNotTurnFinancialValuesIntoZeroes() {
        when(mapper.selectList(any())).thenReturn(List.of(followed("300750")));
        when(agent.marketSnapshot()).thenThrow(new IllegalStateException("private connection data"));
        var failure=assertThrows(BizException.class,()->service.list(7));
        assertFalse(failure.getMessage().contains("private"));
    }

    @Test void followingAnExistingCodeIsIdempotentEvenAtTheLimit() {
        SecurityContextHolder.getContext().setAuthentication(new UsernamePasswordAuthenticationToken(
            new LoginUser(7,"reader","USER","test"),null,List.of()));
        var serviceMock=mock(WatchlistService.class);
        when(mapper.selectCount(any())).thenReturn(1L);
        assertDoesNotThrow(()->new WatchlistController(mapper,serviceMock).add("300750"));
        verifyNoInteractions(serviceMock);
        verify(mapper,never()).insert(any(Watchlist.class));
        verify(mapper,times(1)).selectCount(any());
    }
}
