package com.finsight.auth;

import com.finsight.auth.dto.AuthDtos.LoginRequest;
import com.finsight.auth.dto.AuthDtos.RefreshRequest;
import com.finsight.auth.dto.AuthDtos.RegisterRequest;
import com.finsight.auth.dto.AuthDtos.TokenPairResponse;
import com.finsight.auth.dto.AuthDtos.UserView;
import com.finsight.common.api.ApiResponse;
import com.finsight.common.ratelimit.RateLimit;
import com.finsight.common.ratelimit.RateLimitKey;
import com.finsight.common.security.SecurityUtils;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@Tag(name = "认证", description = "注册 / 登录 / 刷新 / 登出（用户端与管理端分离登录）")
@RestController
@RequestMapping("/api/auth")
@RequiredArgsConstructor
public class AuthController {

    private final AuthService authService;

    @Operation(summary = "用户注册")
    @PostMapping("/register")
    @RateLimit(name = "auth-register", keyBy = RateLimitKey.IP, windowSeconds = 300, limit = 5,
            message = "注册过于频繁，请稍后再试")
    public ApiResponse<UserView> register(@Valid @RequestBody RegisterRequest request) {
        return ApiResponse.ok(authService.register(request));
    }

    @Operation(summary = "用户端登录")
    @PostMapping("/login")
    @RateLimit(name = "auth-login", keyBy = RateLimitKey.IP, windowSeconds = 60, limit = 5,
            message = "登录尝试过于频繁，请稍后再试")
    public ApiResponse<TokenPairResponse> login(@Valid @RequestBody LoginRequest request) {
        return ApiResponse.ok(authService.login(request, false));
    }

    @Operation(summary = "管理端登录（校验 ADMIN 角色）")
    @PostMapping("/admin/login")
    @RateLimit(name = "admin-login", keyBy = RateLimitKey.IP, windowSeconds = 60, limit = 5,
            message = "登录尝试过于频繁，请稍后再试")
    public ApiResponse<TokenPairResponse> adminLogin(@Valid @RequestBody LoginRequest request) {
        return ApiResponse.ok(authService.login(request, true));
    }

    @Operation(summary = "刷新双 token（旋转换发，旧 refresh 即刻失效）")
    @PostMapping("/refresh")
    public ApiResponse<TokenPairResponse> refresh(@Valid @RequestBody RefreshRequest request) {
        return ApiResponse.ok(authService.refresh(request.refreshToken()));
    }

    @Operation(summary = "登出（撤销 refresh + 拉黑 access）")
    @PostMapping("/logout")
    public ApiResponse<Void> logout(@RequestBody(required = false) RefreshRequest request) {
        authService.logout(SecurityUtils.currentUser(), request == null ? null : request.refreshToken());
        return ApiResponse.ok();
    }
}
