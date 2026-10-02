package com.finsight.notify.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.finsight.notify.entity.Notification;
import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface NotificationMapper extends BaseMapper<Notification> {
}
