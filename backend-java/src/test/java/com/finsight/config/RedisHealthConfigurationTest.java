package com.finsight.config;

import org.junit.jupiter.api.Test;
import org.springframework.boot.actuate.health.Status;
import org.springframework.boot.actuate.autoconfigure.data.redis.RedisHealthContributorAutoConfiguration;
import org.springframework.boot.actuate.autoconfigure.data.redis.RedisReactiveHealthContributorAutoConfiguration;
import org.springframework.boot.autoconfigure.AutoConfigurations;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.data.redis.connection.RedisConnection;
import org.springframework.data.redis.connection.RedisConnectionFactory;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

class RedisHealthConfigurationTest {
    @Test
    void pingChecksTheConfiguredConnectionAndClosesItWithoutInfoParsing() {
        var factory=mock(RedisConnectionFactory.class);
        var connection=mock(RedisConnection.class);
        when(factory.getConnection()).thenReturn(connection);
        when(connection.ping()).thenReturn("PONG");
        var health=new RedisHealthConfiguration().redisHealthContributor(factory).health();
        assertThat(health.getStatus()).isEqualTo(Status.UP);
        verify(connection).ping();
        verify(connection).close();
        verifyNoMoreInteractions(connection);
    }

    @Test
    void authenticationOrConnectionFailureStillReportsDownWithoutPrivateErrorText() {
        var factory=mock(RedisConnectionFactory.class);
        when(factory.getConnection()).thenThrow(new IllegalStateException("private-service-value"));
        var health=new RedisHealthConfiguration().redisHealthContributor(factory).health();
        assertThat(health.getStatus()).isEqualTo(Status.DOWN);
        assertThat(health.getDetails().toString()).doesNotContain("private-service-value");
    }

    @Test
    void unexpectedPingReplyReportsDownAndClosesTheConnection() {
        var factory=mock(RedisConnectionFactory.class);
        var connection=mock(RedisConnection.class);
        when(factory.getConnection()).thenReturn(connection);
        when(connection.ping()).thenReturn(null);
        assertThat(new RedisHealthConfiguration().redisHealthContributor(factory).health().getStatus()).isEqualTo(Status.DOWN);
        verify(connection).close();
    }

    @Test
    void customContributorPreventsBothInfoBasedAutoConfiguredIndicators() {
        new ApplicationContextRunner()
            .withConfiguration(AutoConfigurations.of(RedisHealthContributorAutoConfiguration.class,
                RedisReactiveHealthContributorAutoConfiguration.class))
            .withUserConfiguration(RedisHealthConfiguration.class)
            .withBean(org.springframework.data.redis.connection.lettuce.LettuceConnectionFactory.class,
                ()->mock(org.springframework.data.redis.connection.lettuce.LettuceConnectionFactory.class))
            .run(context->{
                assertThat(context).hasBean("redisHealthContributor");
                assertThat(context.getBeansOfType(org.springframework.boot.actuate.health.HealthIndicator.class)).hasSize(1);
                assertThat(context.getBeansOfType(org.springframework.boot.actuate.health.ReactiveHealthContributor.class)).isEmpty();
            });
    }
}
