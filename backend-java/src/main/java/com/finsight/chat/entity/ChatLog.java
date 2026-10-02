package com.finsight.chat.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.time.LocalDateTime;

/** 作品说明：对话审计日志 */
@Data
@TableName("chat_log")
public class ChatLog {

    @TableId(type = IdType.AUTO)
    private Long id;

    private Long userId;

    private String sessionUid;

    private String requestId;

    private String question;

    /** 作品说明：审计状态区分正常、错误与中断。 */
    private String status;

    private Integer durationMs;

    private LocalDateTime createdAt;
}
