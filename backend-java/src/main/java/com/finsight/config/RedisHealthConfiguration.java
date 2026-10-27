package com.finsight.config;

import org.springframework.boot.actuate.health.Health;
import org.springframework.boot.actuate.health.HealthIndicator;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.redis.connection.RedisConnectionFactory;

/** 用实际连接的PING检查Redis，避免原生Windows服务的INFO路径被当作转义配置解析。 */
@Configuration(proxyBeanMethods=false)
public class RedisHealthConfiguration {
    @Bean(name="redisHealthContributor")
    public HealthIndicator redisHealthContributor(RedisConnectionFactory factory) {
        return ()->{
            try (var connection=factory.getConnection()) {
                if (!"PONG".equals(connection.ping())) {
                    return Health.down().withDetail("probe","PING returned an unexpected response").build();
                }
                return Health.up().withDetail("probe","PING").build();
            } catch (RuntimeException failure) {
                return Health.down().withDetail("probe","Redis connection or PING failed").build();
            }
        };
    }
}
