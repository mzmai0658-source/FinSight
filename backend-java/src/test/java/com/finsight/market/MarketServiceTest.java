package com.finsight.market;

import com.finsight.chat.AgentClient;
import com.finsight.common.exception.BizException;
import com.finsight.market.dto.MarketDtos.*;
import com.finsight.meta.MetaController;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.data.redis.core.StringRedisTemplate;
import java.util.List;
import java.util.Map;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class MarketServiceTest {
    private final AgentClient agent = mock(AgentClient.class);
    private final StringRedisTemplate redis = mock(StringRedisTemplate.class);
    private final MarketService service = new MarketService(agent, redis);

    private MarketSnapshot snapshot(String code, String name) {
        var company = new CompanyItem(code, name, "", "", "", "", "", "试验财报库", "imported", 2023, 2025);
        var dataset = new DatasetInfo(code, true, "试验财报库", 12, 1, false);
        var detail = new CompanyDetail(company, "", "", null, Map.of("year",2025), List.of(), dataset);
        var series = new CompanySeries(code, List.of(), List.of(), List.of());
        var summary = new MarketSummary(1,0,2025,List.of(),List.of(),List.of(),List.of(),dataset);
        return new MarketSnapshot(List.of(company), summary, Map.of(code, detail), Map.of(code, series),
                List.of(Map.of("id", "ex-"+code, "question", name+"2025年合并营业收入是多少？", "type", "查年报")));
    }

    @Test void companyListAndDetailFollowTheSelectedReleaseWithoutLegacyCache() {
        when(agent.marketSnapshot()).thenReturn(snapshot("300750", "宁德时代"));
        assertEquals("300750", service.listCompanies("宁德", null, "imported").get(0).stockCode());
        assertTrue(service.listCompanies("库外公司", null, null).isEmpty());
        assertEquals("宁德时代", service.companyDetail("300750").profile().abbr());
        assertEquals("300750", service.companySeries("300750").stockCode());
        assertThrows(BizException.class, () -> service.companyDetail("999999"));
        assertThrows(BizException.class, () -> service.companySeries("999999"));
        when(agent.marketSnapshot()).thenReturn(snapshot("600900", "长江电力"));
        assertEquals("600900", service.listCompanies(null, null, null).get(0).stockCode());
        verifyNoInteractions(redis);
    }

    @Test void statisticsFailureDoesNotHideTheSelectedCatalogue() {
        when(agent.marketSnapshot()).thenReturn(snapshot("300750", "宁德时代"));
        when(redis.opsForZSet()).thenThrow(new IllegalStateException("redis unavailable"));
        assertEquals(1, service.summary().importedCompanies());
        assertTrue(service.summary().dataset().experimental());
    }

    @Test void unavailableSelectedReleaseDoesNotFallBackToLegacyCompanies() {
        when(agent.marketSnapshot()).thenThrow(new IllegalStateException("private database details"));
        var error = assertThrows(BizException.class, () -> service.listCompanies(null,null,null));
        assertFalse(error.getMessage().contains("private"));
        verifyNoInteractions(redis);
    }

    @Test void examplesUseCanonicalCatalogueAndHaveSafeUnavailableFallback() throws Exception {
        var mapper = new ObjectMapper();
        var controller = new MetaController(agent, mapper);
        when(agent.marketSnapshot()).thenReturn(snapshot("300750", "宁德时代"));
        assertTrue(mapper.writeValueAsString(controller.examples()).contains("宁德时代2025"));
        when(agent.marketSnapshot()).thenThrow(new IllegalStateException("offline"));
        assertTrue(mapper.writeValueAsString(controller.examples()).contains("ex-help"));
    }
}
