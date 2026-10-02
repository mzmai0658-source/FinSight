package com.finsight.market.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.time.LocalDateTime;

/** 作品说明：上市公司主数据（公司体系唯一事实来源） */
@Data
@TableName("company")
public class Company {

    @TableId(type = IdType.AUTO)
    private Long id;

    private String stockCode;

    private String abbr;

    private String fullName;

    private String enName;

    private String exchange;

    private String board;

    private String industry;

    private String region;

    private String regCapital;

    private Integer employees;

    /** 作品说明：数据集标记：医药 / 中药 */
    private String datasetTag;

    /** 作品说明：imported=财务数据已入库 pending=待导入 */
    private String dataStatus;

    private Integer firstYear;

    private Integer lastYear;

    private LocalDateTime createdAt;

    private LocalDateTime updatedAt;
}
