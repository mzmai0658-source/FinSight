package com.finsight.auth.jwt;

import com.finsight.common.api.ErrorCode;
import com.finsight.common.exception.BizException;
import io.jsonwebtoken.Claims;
import io.jsonwebtoken.ExpiredJwtException;
import io.jsonwebtoken.JwtException;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import jakarta.annotation.PostConstruct;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Date;
import java.util.UUID;

/** 作品说明：JWT 双 token：access 用于接口鉴权（短期），refresh 用于换发（长期、Redis 可撤销）。 */
@Component
@RequiredArgsConstructor
public class JwtTokenProvider {

    public static final String CLAIM_TYPE = "type";
    public static final String CLAIM_ROLE = "role";
    public static final String CLAIM_USERNAME = "username";
    public static final String TYPE_ACCESS = "access";
    public static final String TYPE_REFRESH = "refresh";

    private final JwtProperties properties;
    private SecretKey key;

    @PostConstruct
    void init() {
        this.key = Keys.hmacShaKeyFor(properties.getSecret().getBytes(StandardCharsets.UTF_8));
    }

    public String createAccessToken(long userId, String username, String role) {
        return buildToken(userId, properties.getAccessTokenTtl().toMillis(), TYPE_ACCESS, username, role);
    }

    public String createRefreshToken(long userId, String username, String role) {
        return buildToken(userId, properties.getRefreshTokenTtl().toMillis(), TYPE_REFRESH, username, role);
    }

    private String buildToken(long userId, long ttlMillis, String type, String username, String role) {
        Instant now = Instant.now();
        return Jwts.builder()
                .id(UUID.randomUUID().toString().replace("-", ""))
                .subject(String.valueOf(userId))
                .issuer(properties.getIssuer())
                .issuedAt(Date.from(now))
                .expiration(Date.from(now.plusMillis(ttlMillis)))
                .claim(CLAIM_TYPE, type)
                .claim(CLAIM_USERNAME, username)
                .claim(CLAIM_ROLE, role)
                .signWith(key)
                .compact();
    }

    /** 作品说明：解析并校验签名/有效期/签发者，过期与非法分别映射到不同错误码。 */
    public Claims parse(String token) {
        try {
            return Jwts.parser()
                    .verifyWith(key)
                    .requireIssuer(properties.getIssuer())
                    .build()
                    .parseSignedClaims(token)
                    .getPayload();
        } catch (ExpiredJwtException e) {
            throw new BizException(ErrorCode.TOKEN_EXPIRED);
        } catch (JwtException | IllegalArgumentException e) {
            throw new BizException(ErrorCode.TOKEN_INVALID);
        }
    }

    public Claims parseAccessToken(String token) {
        Claims claims = parse(token);
        if (!TYPE_ACCESS.equals(claims.get(CLAIM_TYPE, String.class))) {
            throw new BizException(ErrorCode.TOKEN_INVALID, "需要 access token");
        }
        return claims;
    }

    public Claims parseRefreshToken(String token) {
        Claims claims = parse(token);
        if (!TYPE_REFRESH.equals(claims.get(CLAIM_TYPE, String.class))) {
            throw new BizException(ErrorCode.TOKEN_INVALID, "需要 refresh token");
        }
        return claims;
    }
}
