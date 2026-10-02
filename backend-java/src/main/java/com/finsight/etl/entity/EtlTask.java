package com.finsight.etl.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.time.LocalDateTime;

/**
 * 作品说明：ETL 任务状态机：PENDING → RUNNING → SUCCESS / PARTIAL / FAILED。
 * 管理端上传后入队，MQ 消费者串行调用 Python 单文件管线并回写状态。
 */
@Data
@TableName("etl_task")
public class EtlTask {

    @TableId(type = IdType.AUTO)
    private Long id;

    private String taskUid;

    private String fileName;

    private String filePath;

    /** 作品说明：任务材料类别区分财报与研报。 */
    private String fileType;

    private String status;

    /** 作品说明：通过执行代际令牌阻止旧工作线程结束新一轮重试。 */
    private String runToken;

    /** 作品说明：基础设施故障由监听器按规则重试，业务失败须显式重试。 */
    private Boolean retryable;

    private Integer attemptCount;

    private String stockCode;

    private Integer reportYear;

    private String message;

    /** 作品说明：Python 管线步骤明细 JSON */
    private String stepsJson;

    private Long createdBy;

    private LocalDateTime startedAt;

    private LocalDateTime finishedAt;

    private LocalDateTime createdAt;

    private LocalDateTime updatedAt;
}
