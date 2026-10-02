package com.finsight.admin;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.finsight.chat.entity.ChatLog;
import com.finsight.chat.entity.ChatSession;
import com.finsight.chat.mapper.ChatLogMapper;
import com.finsight.chat.mapper.ChatSessionMapper;
import com.finsight.common.api.ApiResponse;
import com.finsight.common.api.ErrorCode;
import com.finsight.common.exception.BizException;
import com.finsight.common.security.SecurityUtils;
import com.finsight.etl.EtlTaskService;
import com.finsight.market.MarketService;
import com.finsight.user.entity.SysUser;
import com.finsight.user.mapper.SysUserMapper;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDate;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** 作品说明：管理端看板与用户管理（/api/admin/** 由安全配置限 ADMIN 角色） */
@Tag(name = "管理端", description = "运营看板 / 用户管理 / 审计日志")
@RestController
@RequestMapping("/api/admin")
@RequiredArgsConstructor
public class AdminController {

    private final SysUserMapper userMapper;
    private final ChatSessionMapper sessionMapper;
    private final ChatLogMapper chatLogMapper;
    private final EtlTaskService etlTaskService;
    private final MarketService marketService;

    public record AdminUserView(long id, String username, String nickname, String role,
                                String riskProfile, int status, String createdAt) {

        static AdminUserView from(SysUser user) {
            return new AdminUserView(user.getId(), user.getUsername(), user.getNickname(),
                    user.getRole(), user.getRiskProfile(), user.getStatus(),
                    user.getCreatedAt() == null ? "" : user.getCreatedAt().toString());
        }
    }

    @Operation(summary = "运营看板：用户/会话/对话/UV/任务概览")
    @GetMapping("/overview")
    public ApiResponse<Map<String, Object>> overview() {
        LocalDate today = LocalDate.now();
        Map<String, Object> data = new HashMap<>();
        data.put("userCount", userMapper.selectCount(null));
        data.put("sessionCount", sessionMapper.selectCount(null));
        data.put("chatTotal", chatLogMapper.selectCount(null));
        data.put("chatToday", chatLogMapper.selectCount(new LambdaQueryWrapper<ChatLog>()
                .ge(ChatLog::getCreatedAt, today.atStartOfDay())));
        data.put("uvToday", marketService.todayUv());
        data.put("etlStats", etlTaskService.statusStats());
        data.put("hotCompanies", marketService.hotCompanies(8));
        return ApiResponse.ok(data);
    }

    @Operation(summary = "用户分页列表")
    @GetMapping("/users")
    public ApiResponse<Map<String, Object>> users(@RequestParam(defaultValue = "1") int page,
                                                  @RequestParam(defaultValue = "10") int size,
                                                  @RequestParam(required = false) String keyword) {
        LambdaQueryWrapper<SysUser> wrapper = new LambdaQueryWrapper<SysUser>().orderByDesc(SysUser::getId);
        if (keyword != null && !keyword.isBlank()) {
            String kw = keyword.trim();
            wrapper.and(w -> w.like(SysUser::getUsername, kw).or().like(SysUser::getNickname, kw));
        }
        Page<SysUser> result = userMapper.selectPage(Page.of(page, Math.min(size, 50)), wrapper);
        List<AdminUserView> records = result.getRecords().stream().map(AdminUserView::from).toList();
        return ApiResponse.ok(Map.of("total", result.getTotal(), "page", page, "records", records));
    }

    @Operation(summary = "启用/禁用用户")
    @PutMapping("/users/{id}/status")
    public ApiResponse<Void> updateStatus(@PathVariable long id, @RequestParam int status) {
        if (id == SecurityUtils.currentUserId()) {
            throw new BizException(ErrorCode.BAD_REQUEST, "不能操作自己的账号");
        }
        SysUser user = userMapper.selectById(id);
        if (user == null) {
            throw new BizException(ErrorCode.NOT_FOUND, "用户不存在");
        }
        if ("ADMIN".equals(user.getRole())) {
            throw new BizException(ErrorCode.BAD_REQUEST, "不能禁用管理员账号");
        }
        user.setStatus(status == 1 ? 1 : 0);
        userMapper.updateById(user);
        return ApiResponse.ok(null);
    }

    @Operation(summary = "对话审计日志分页")
    @GetMapping("/chat-logs")
    public ApiResponse<Map<String, Object>> chatLogs(@RequestParam(defaultValue = "1") int page,
                                                     @RequestParam(defaultValue = "10") int size) {
        Page<ChatLog> result = chatLogMapper.selectPage(Page.of(page, Math.min(size, 50)),
                new LambdaQueryWrapper<ChatLog>().orderByDesc(ChatLog::getId));
        return ApiResponse.ok(Map.of("total", result.getTotal(), "page", page, "records", result.getRecords()));
    }
}
