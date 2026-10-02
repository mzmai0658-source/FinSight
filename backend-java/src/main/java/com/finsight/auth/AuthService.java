package com.finsight.auth;

import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.finsight.auth.dto.AuthDtos.LoginRequest;
import com.finsight.auth.dto.AuthDtos.RegisterRequest;
import com.finsight.auth.dto.AuthDtos.TokenPairResponse;
import com.finsight.auth.dto.AuthDtos.UserView;
import com.finsight.auth.jwt.JwtProperties;
import com.finsight.auth.jwt.JwtTokenProvider;
import com.finsight.auth.jwt.TokenStore;
import com.finsight.common.api.ErrorCode;
import com.finsight.common.exception.BizException;
import com.finsight.user.entity.SysUser;
import com.finsight.user.mapper.SysUserMapper;
import io.jsonwebtoken.Claims;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.time.Instant;

@Slf4j
@Service
@RequiredArgsConstructor
public class AuthService {

    private final SysUserMapper userMapper;
    private final PasswordEncoder passwordEncoder;
    private final JwtTokenProvider tokenProvider;
    private final TokenStore tokenStore;
    private final JwtProperties jwtProperties;

    public UserView register(RegisterRequest request) {
        Long existing = userMapper.selectCount(
                Wrappers.<SysUser>lambdaQuery().eq(SysUser::getUsername, request.username()));
        if (existing != null && existing > 0) {
            throw new BizException(ErrorCode.USERNAME_TAKEN);
        }
        SysUser user = new SysUser();
        user.setUsername(request.username());
        user.setPasswordHash(passwordEncoder.encode(request.password()));
        user.setNickname(request.nickname() == null || request.nickname().isBlank()
                ? request.username() : request.nickname().trim());
        user.setRole(SysUser.ROLE_USER);
        userMapper.insert(user);
        log.info("新用户注册: {}", user.getUsername());
        return toView(user);
    }

    public TokenPairResponse login(LoginRequest request, boolean requireAdmin) {
        SysUser user = userMapper.selectOne(
                Wrappers.<SysUser>lambdaQuery().eq(SysUser::getUsername, request.username()));
        if (user == null || !passwordEncoder.matches(request.password(), user.getPasswordHash())) {
            throw new BizException(ErrorCode.BAD_CREDENTIALS);
        }
        if (user.getStatus() == null || user.getStatus() != 1) {
            throw new BizException(ErrorCode.USER_DISABLED);
        }
        if (requireAdmin && !SysUser.ROLE_ADMIN.equals(user.getRole())) {
            throw new BizException(ErrorCode.NOT_ADMIN);
        }
        return issueTokenPair(user);
    }

    /** 作品说明：refresh token 旋转换发：旧 refresh 立即失效，防止重放。 */
    public TokenPairResponse refresh(String refreshToken) {
        Claims claims = tokenProvider.parseRefreshToken(refreshToken);
        long userId = Long.parseLong(claims.getSubject());
        if (!tokenStore.isRefreshTokenValid(userId, claims.getId())) {
            throw new BizException(ErrorCode.TOKEN_INVALID, "refresh token 已失效，请重新登录");
        }
        tokenStore.revokeRefreshToken(userId, claims.getId());

        SysUser user = userMapper.selectById(userId);
        if (user == null || user.getStatus() == null || user.getStatus() != 1) {
            throw new BizException(ErrorCode.USER_DISABLED);
        }
        return issueTokenPair(user);
    }

    public void logout(LoginUser loginUser, String refreshToken) {
        if (refreshToken != null && !refreshToken.isBlank()) {
            try {
                Claims claims = tokenProvider.parseRefreshToken(refreshToken);
                tokenStore.revokeRefreshToken(Long.parseLong(claims.getSubject()), claims.getId());
            } catch (BizException ignored) {
                // 作品说明：refresh 已过期/非法则无需撤销
            }
        }
        // 作品说明：access token 拉黑剩余有效期
        tokenStore.blacklistAccessToken(loginUser.tokenId(), jwtProperties.getAccessTokenTtl());
    }

    private TokenPairResponse issueTokenPair(SysUser user) {
        String access = tokenProvider.createAccessToken(user.getId(), user.getUsername(), user.getRole());
        String refresh = tokenProvider.createRefreshToken(user.getId(), user.getUsername(), user.getRole());
        Claims refreshClaims = tokenProvider.parseRefreshToken(refresh);
        Duration ttl = Duration.between(Instant.now(), refreshClaims.getExpiration().toInstant());
        tokenStore.saveRefreshToken(user.getId(), refreshClaims.getId(), ttl);
        return new TokenPairResponse(access, refresh, jwtProperties.getAccessTokenTtl().toSeconds(), toView(user));
    }

    private UserView toView(SysUser user) {
        return new UserView(user.getId(), user.getUsername(), user.getNickname(), user.getRole(),
                user.getRiskProfile());
    }
}
