package com.finsight.advisor.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.time.LocalDateTime;

@Data
@TableName("advisor_report")
public class AdvisorReport {

    public static final String STATUS_GENERATING = "GENERATING";
    public static final String STATUS_READY = "READY";
    public static final String STATUS_FAILED = "FAILED";

    @TableId(type = IdType.AUTO)
    private Long id;

    private Long userId;

    private String stockCode;

    private String stockName;

    private String riskProfile;

    /** 作品说明：规则引擎总分 0-100 */
    private Integer score;

    private String rating;

    /** 作品说明：分维度评分明细 JSON */
    private String dimensions;

    /** 作品说明：LLM 生成的 Markdown 报告 */
    private String reportMd;

    private String status;

    private LocalDateTime createdAt;

    private LocalDateTime updatedAt;
}
