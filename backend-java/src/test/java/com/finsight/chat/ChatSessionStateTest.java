package com.finsight.chat;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.finsight.chat.entity.ChatMessage;
import com.finsight.chat.mapper.ChatMessageMapper;
import com.finsight.chat.mapper.ChatSessionMapper;
import org.junit.jupiter.api.Test;
import java.util.List;
import java.util.Map;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.any;

class ChatSessionStateTest {
    @Test void historyCarriesServerStateAndDoesNotInventLegacyState() {
        var messages = mock(ChatMessageMapper.class);
        var service = new ChatSessionService(mock(ChatSessionMapper.class), messages, new ObjectMapper(), mock(AgentClient.class));
        var modern = new ChatMessage(); modern.setRole("assistant"); modern.setContent("使用说明");
        modern.setMetadata("{\"response_kind\":\"conversation\",\"dialogue_state\":{\"version\":1},\"sql\":\"not history evidence\"}");
        var legacy = new ChatMessage(); legacy.setRole("assistant"); legacy.setContent("旧回答");
        legacy.setMetadata("{\"query_plan\":{\"codes\":[\"002082\"]}}");
        var user = new ChatMessage(); user.setRole("user"); user.setContent("继续");
        user.setMetadata(modern.getMetadata());
        when(messages.selectList(any())).thenReturn(List.of(user, modern, legacy));
        var history = service.buildHistory(1L);
        assertFalse(history.get(0).containsKey("metadata"));
        var state = (Map<?,?>) history.get(1).get("metadata");
        assertEquals("conversation", ((com.fasterxml.jackson.databind.JsonNode) state.get("response_kind")).asText());
        assertTrue(state.containsKey("dialogue_state"));
        assertFalse(state.containsKey("sql"));
        assertFalse(history.get(2).containsKey("metadata"));
    }
}
