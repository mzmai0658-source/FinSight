package com.finsight.auth.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

public final class AuthDtos {

    private AuthDtos() {
    }

    public record RegisterRequest(
            @NotBlank @Size(min = 3, max = 32)
            @Pattern(regexp = "^[a-zA-Z0-9_]+$", message = "仅支持字母、数字、下划线")
            String username,
            @NotBlank @Size(min = 6, max = 64)
            String password,
            @Size(max = 32)
            String nickname) {
    }

    public record LoginRequest(
            @NotBlank String username,
            @NotBlank String password) {
    }

    public record RefreshRequest(@NotBlank String refreshToken) {
    }

    public record UserView(long id, String username, String nickname, String role, String riskProfile) {
    }

    public record TokenPairResponse(String accessToken, String refreshToken, long accessExpiresInSeconds,
                                    UserView user) {
    }
}
