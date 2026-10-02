package com.finsight.auth.jwt;

import com.finsight.auth.LoginUser;
import com.finsight.common.exception.BizException;
import io.jsonwebtoken.Claims;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.List;

/**
 * 作品说明：解析 Authorization: Bearer <access token>，构建 SecurityContext。
 * token 缺失/无效时不在此处拒绝，交由 SecurityConfig 的授权规则与入口点统一处理。
 */
@Component
@RequiredArgsConstructor
public class JwtAuthenticationFilter extends OncePerRequestFilter {

    private static final String BEARER_PREFIX = "Bearer ";

    private final JwtTokenProvider tokenProvider;
    private final TokenStore tokenStore;

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String header = request.getHeader("Authorization");
        if (header != null && header.startsWith(BEARER_PREFIX)) {
            String token = header.substring(BEARER_PREFIX.length()).trim();
            try {
                Claims claims = tokenProvider.parseAccessToken(token);
                if (!tokenStore.isAccessTokenBlacklisted(claims.getId())) {
                    long userId = Long.parseLong(claims.getSubject());
                    String username = claims.get(JwtTokenProvider.CLAIM_USERNAME, String.class);
                    String role = claims.get(JwtTokenProvider.CLAIM_ROLE, String.class);
                    LoginUser loginUser = new LoginUser(userId, username, role, claims.getId());
                    UsernamePasswordAuthenticationToken authentication = new UsernamePasswordAuthenticationToken(
                            loginUser, null, List.of(new SimpleGrantedAuthority("ROLE_" + role)));
                    SecurityContextHolder.getContext().setAuthentication(authentication);
                }
            } catch (BizException ignored) {
                // 作品说明：无效 token 视作未登录，由授权规则决定是否放行
            }
        }
        chain.doFilter(request, response);
    }
}
