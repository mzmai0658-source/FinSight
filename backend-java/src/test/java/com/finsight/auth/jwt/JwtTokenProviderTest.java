package com.finsight.auth.jwt;

import com.finsight.common.exception.BizException;
import io.jsonwebtoken.Claims;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.lang.reflect.Method;
import java.time.Duration;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/** 作品说明：JWT 双 token 单测：签发/解析往返、access 与 refresh 类型隔离、过期与篡改拒绝。 */
class JwtTokenProviderTest {

    private static final String SECRET = "unit-test-secret-key-0123456789abcdef-0123456789abcdef";

    private JwtTokenProvider provider;
    private JwtProperties properties;

    @BeforeEach
    void setUp() throws Exception {
        properties = new JwtProperties();
        properties.setSecret(SECRET);
        properties.setAccessTokenTtl(Duration.ofMinutes(5));
        properties.setRefreshTokenTtl(Duration.ofDays(1));
        provider = newProvider(properties);
    }

    private static JwtTokenProvider newProvider(JwtProperties props) throws Exception {
        JwtTokenProvider p = new JwtTokenProvider(props);
        Method init = JwtTokenProvider.class.getDeclaredMethod("init");
        init.setAccessible(true);
        init.invoke(p);
        return p;
    }

    @Test
    void accessTokenRoundTrip() {
        String token = provider.createAccessToken(42L, "alice", "USER");

        Claims claims = provider.parseAccessToken(token);

        assertThat(claims.getSubject()).isEqualTo("42");
        assertThat(claims.get(JwtTokenProvider.CLAIM_USERNAME, String.class)).isEqualTo("alice");
        assertThat(claims.get(JwtTokenProvider.CLAIM_ROLE, String.class)).isEqualTo("USER");
        assertThat(claims.getId()).isNotBlank(); // 作品说明：jti 用于 Redis 撤销
    }

    @Test
    void refreshTokenCannotBeUsedAsAccessToken() {
        String refresh = provider.createRefreshToken(1L, "bob", "USER");

        assertThatThrownBy(() -> provider.parseAccessToken(refresh))
                .isInstanceOf(BizException.class);
        // 作品说明：反方向同样拒绝
        String access = provider.createAccessToken(1L, "bob", "USER");
        assertThatThrownBy(() -> provider.parseRefreshToken(access))
                .isInstanceOf(BizException.class);
    }

    @Test
    void expiredTokenIsRejected() throws Exception {
        JwtProperties expired = new JwtProperties();
        expired.setSecret(SECRET);
        expired.setAccessTokenTtl(Duration.ofMillis(-1000)); // 作品说明：签发即过期
        JwtTokenProvider expiredProvider = newProvider(expired);

        String token = expiredProvider.createAccessToken(7L, "carol", "USER");

        assertThatThrownBy(() -> expiredProvider.parse(token))
                .isInstanceOf(BizException.class);
    }

    @Test
    void tamperedTokenIsRejected() {
        String token = provider.createAccessToken(9L, "dave", "ADMIN");
        String tampered = token.substring(0, token.length() - 4) + "xxxx";

        assertThatThrownBy(() -> provider.parse(tampered))
                .isInstanceOf(BizException.class);
    }

    @Test
    void differentSecretCannotParse() throws Exception {
        JwtProperties other = new JwtProperties();
        other.setSecret("another-secret-key-0123456789abcdef-0123456789abcdef");
        JwtTokenProvider otherProvider = newProvider(other);

        String token = provider.createAccessToken(11L, "eve", "USER");

        assertThatThrownBy(() -> otherProvider.parse(token))
                .isInstanceOf(BizException.class);
    }
}
