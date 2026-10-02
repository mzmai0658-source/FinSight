package com.finsight.notify;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.finsight.common.api.ApiResponse;
import com.finsight.common.security.SecurityUtils;
import com.finsight.notify.entity.Notification;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@Tag(name = "站内信", description = "通知列表与已读管理")
@RestController
@RequestMapping("/api/notifications")
@RequiredArgsConstructor
public class NotificationController {

    private final NotificationService notificationService;

    @Operation(summary = "通知分页列表")
    @GetMapping
    public ApiResponse<Map<String, Object>> list(@RequestParam(defaultValue = "1") int page,
                                                 @RequestParam(defaultValue = "10") int size) {
        Page<Notification> result = notificationService.page(SecurityUtils.currentUserId(), page, Math.min(size, 50));
        return ApiResponse.ok(Map.of(
                "total", result.getTotal(),
                "page", page,
                "records", result.getRecords()));
    }

    @Operation(summary = "未读数量")
    @GetMapping("/unread-count")
    public ApiResponse<Long> unreadCount() {
        return ApiResponse.ok(notificationService.unreadCount(SecurityUtils.currentUserId()));
    }

    @Operation(summary = "标记单条已读")
    @PostMapping("/{id}/read")
    public ApiResponse<Void> markRead(@PathVariable long id) {
        notificationService.markRead(SecurityUtils.currentUserId(), id);
        return ApiResponse.ok(null);
    }

    @Operation(summary = "全部已读")
    @PostMapping("/read-all")
    public ApiResponse<Void> markAllRead() {
        notificationService.markAllRead(SecurityUtils.currentUserId());
        return ApiResponse.ok(null);
    }
}
