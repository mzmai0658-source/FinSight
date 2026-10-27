package com.finsight.chat;

import com.baomidou.mybatisplus.core.MybatisConfiguration;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.metadata.TableInfoHelper;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.finsight.chat.entity.ChatSession;
import com.finsight.chat.mapper.ChatMessageMapper;
import com.finsight.chat.mapper.ChatSessionMapper;
import org.apache.ibatis.builder.MapperBuilderAssistant;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.mockito.ArgumentCaptor;
import org.springframework.boot.env.YamlPropertySourceLoader;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.core.io.ClassPathResource;

import java.util.List;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class ChatSessionRetentionTest {
    private final ChatSessionMapper sessions = mock(ChatSessionMapper.class);
    private final ChatMessageMapper messages = mock(ChatMessageMapper.class);
    private final AgentClient agent = mock(AgentClient.class);

    @BeforeAll static void tableInfo() {
        TableInfoHelper.initTableInfo(new MapperBuilderAssistant(new MybatisConfiguration(), "retention"), ChatSession.class);
    }

    @Test void configuredEnvironmentLimitPreservesAll140EvaluationSessions() {
        when(sessions.selectCount(any())).thenReturn(139L);
        configuration().withPropertyValues("CHAT_MAX_SESSIONS_PER_USER=200").run(context -> {
            assertThat(context).hasNotFailed();
            ChatSessionService service = context.getBean(ChatSessionService.class);
            service.create(7L);
            verify(sessions, never()).selectList(any());
            verify(sessions, never()).deleteById(anyLong());
            verifyNoInteractions(messages, agent);
            when(sessions.selectList(any())).thenReturn(List.of());
            service.list(7L);
            assertListLimit(200);
        });
    }

    @Test void defaultLimitStillPrunesAt100Sessions() {
        when(sessions.selectCount(any())).thenReturn(100L);
        ChatSession oldest = new ChatSession(); oldest.setId(1L);
        when(sessions.selectList(any())).thenReturn(List.of(oldest));
        when(sessions.lockExisting(1L)).thenReturn(1L);
        new ApplicationContextRunner().withBean(ChatSessionService.class, this::service).run(context -> {
            assertThat(context).hasNotFailed();
            context.getBean(ChatSessionService.class).create(7L);
            verify(sessions).deleteById(1L);
            verify(messages).delete(any());
            assertListLimit(1);
        });
    }

    @ParameterizedTest @ValueSource(ints = {1, 1000})
    void boundaryLimitsAreAcceptedAndUsedForListing(int limit) {
        when(sessions.selectList(any())).thenReturn(List.of());
        new ApplicationContextRunner().withBean(ChatSessionService.class, this::service)
                .withPropertyValues("finsight.chat.max-sessions-per-user=" + limit).run(context -> {
                    assertThat(context).hasNotFailed();
                    context.getBean(ChatSessionService.class).list(7L);
                    assertListLimit(limit);
                });
    }

    @ParameterizedTest @ValueSource(ints = {0, 1001})
    void outOfRangeConfigurationFailsBeforeHistoryCanBePruned(int limit) {
        new ApplicationContextRunner().withBean(ChatSessionService.class, this::service)
                .withPropertyValues("finsight.chat.max-sessions-per-user=" + limit).run(context -> {
                    assertThat(context).hasFailed();
                    assertThat(context.getStartupFailure()).hasRootCauseInstanceOf(IllegalArgumentException.class)
                            .hasStackTraceContaining("between 1 and 1000");
                    verifyNoInteractions(sessions, messages, agent);
                });
    }

    private ChatSessionService service() { return new ChatSessionService(sessions, messages, new ObjectMapper(), agent); }

    private ApplicationContextRunner configuration() {
        return new ApplicationContextRunner().withBean(ChatSessionService.class, this::service).withInitializer(context -> {
            try {
                for (var source : new YamlPropertySourceLoader().load("application", new ClassPathResource("application.yml"))) {
                    context.getEnvironment().getPropertySources().addLast(source);
                }
            } catch (java.io.IOException e) { throw new IllegalStateException(e); }
        });
    }

    private void assertListLimit(int limit) {
        var capture = ArgumentCaptor.forClass(LambdaQueryWrapper.class);
        verify(sessions).selectList(capture.capture());
        assertThat(capture.getValue().getSqlSegment()).endsWith("LIMIT " + limit);
    }
}
