package com.finsight.notify;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.finsight.notify.entity.Notification;
import com.finsight.notify.mapper.NotificationMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

@Slf4j
@Service
@RequiredArgsConstructor
public class NotificationService {

    private final NotificationMapper notificationMapper;

    /** 作品说明：发送站内信（异步链路调用，失败不抛出，不影响主流程） */
    public void send(long userId, String type, String title, String content, String link) {
        try {
            Notification notification = new Notification();
            notification.setUserId(userId);
            notification.setType(type);
            notification.setTitle(title);
            notification.setContent(content == null ? "" : content);
            notification.setLink(link == null ? "" : link);
            notification.setReadFlag(0);
            notificationMapper.insert(notification);
        } catch (Exception e) {
            log.warn("[notify] 站内信发送失败 userId={} title={}: {}", userId, title, e.toString());
        }
    }

    public Page<Notification> page(long userId, int page, int size) {
        return notificationMapper.selectPage(Page.of(page, size),
                new LambdaQueryWrapper<Notification>()
                        .eq(Notification::getUserId, userId)
                        .orderByDesc(Notification::getId));
    }

    public long unreadCount(long userId) {
        return notificationMapper.selectCount(new LambdaQueryWrapper<Notification>()
                .eq(Notification::getUserId, userId)
                .eq(Notification::getReadFlag, 0));
    }

    public void markRead(long userId, long id) {
        notificationMapper.update(null, new LambdaUpdateWrapper<Notification>()
                .eq(Notification::getId, id)
                .eq(Notification::getUserId, userId)
                .set(Notification::getReadFlag, 1));
    }

    public void markAllRead(long userId) {
        notificationMapper.update(null, new LambdaUpdateWrapper<Notification>()
                .eq(Notification::getUserId, userId)
                .eq(Notification::getReadFlag, 0)
                .set(Notification::getReadFlag, 1));
    }
}
