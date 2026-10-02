package com.finsight.user;

import com.finsight.auth.dto.AuthDtos.UserView;
import com.finsight.common.api.ApiResponse;
import com.finsight.common.api.ErrorCode;
import com.finsight.common.exception.BizException;
import com.finsight.common.security.SecurityUtils;
import com.finsight.user.entity.SysUser;
import com.finsight.user.mapper.SysUserMapper;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import lombok.RequiredArgsConstructor;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@Tag(name = "用户", description = "当前用户信息")
@Validated
@RestController
@RequestMapping("/api/users")
@RequiredArgsConstructor
public class UserController {

    private final SysUserMapper userMapper;

    public record UpdateProfileRequest(
            @Size(max = 32, message = "昵称最长 32 个字符") String nickname,
            @Pattern(regexp = "conservative|balanced|aggressive", message = "风险偏好取值不合法") String riskProfile) {
    }

    @Operation(summary = "当前登录用户")
    @GetMapping("/me")
    public ApiResponse<UserView> me() {
        SysUser user = currentUser();
        return ApiResponse.ok(toView(user));
    }

    @Operation(summary = "更新资料（昵称 / 风险偏好）")
    @PutMapping("/me")
    public ApiResponse<UserView> update(@RequestBody @Validated UpdateProfileRequest request) {
        SysUser user = currentUser();
        if (request.nickname() != null && !request.nickname().isBlank()) {
            user.setNickname(request.nickname().trim());
        }
        if (request.riskProfile() != null && !request.riskProfile().isBlank()) {
            user.setRiskProfile(request.riskProfile());
        }
        userMapper.updateById(user);
        return ApiResponse.ok(toView(user));
    }

    private SysUser currentUser() {
        SysUser user = userMapper.selectById(SecurityUtils.currentUserId());
        if (user == null) {
            throw new BizException(ErrorCode.UNAUTHORIZED);
        }
        return user;
    }

    private static UserView toView(SysUser user) {
        return new UserView(user.getId(), user.getUsername(), user.getNickname(),
                user.getRole(), user.getRiskProfile());
    }
}
