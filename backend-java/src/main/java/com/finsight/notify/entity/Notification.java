package com.finsight.notify.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.time.LocalDateTime;

@Data
@TableName("notification")
public class Notification {

    @TableId(type = IdType.AUTO)
    private Long id;

    private Long userId;

    /** 作品说明：站内信类型区分系统、诊股报告与数据导入事件。 */
    private String type;

    private String title;

    private String content;

    /** 作品说明：前端跳转路径，如 /market/603259 */
    private String link;

    private Integer readFlag;

    private LocalDateTime createdAt;
}
