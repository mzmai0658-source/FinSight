package com.finsight.auth.jwt;

import lombok.RequiredArgsConstructor;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;

import java.time.Duration;

/**
 * 作品说明：Redis 维护 token 生命周期：
 * - refresh token 白名单（按 jti），换发时旋转、登出时删除 → refresh 可主动撤销
 * - access token 黑名单（登出后剩余 TTL 内拒绝）
 */
@Component
@RequiredArgsConstructor
public class TokenStore {

    private static final String REFRESH_KEY = "auth:refresh:%d:%s";
    private static final String BLACKLIST_KEY = "auth:blacklist:%s";

    private final StringRedisTemplate redisTemplate;

    public void saveRefreshToken(long userId, String jti, Duration ttl) {
        redisTemplate.opsForValue().set(REFRESH_KEY.formatted(userId, jti), "1", ttl);
    }

    public boolean isRefreshTokenValid(long userId, String jti) {
        return Boolean.TRUE.equals(redisTemplate.hasKey(REFRESH_KEY.formatted(userId, jti)));
    }

    public void revokeRefreshToken(long userId, String jti) {
        redisTemplate.delete(REFRESH_KEY.formatted(userId, jti));
    }

    public void blacklistAccessToken(String jti, Duration remainingTtl) {
        if (remainingTtl.isNegative() || remainingTtl.isZero()) {
            return;
        }
        redisTemplate.opsForValue().set(BLACKLIST_KEY.formatted(jti), "1", remainingTtl);
    }

    public boolean isAccessTokenBlacklisted(String jti) {
        return Boolean.TRUE.equals(redisTemplate.hasKey(BLACKLIST_KEY.formatted(jti)));
    }
}
