package com.finsight.report.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.time.LocalDate;
import java.time.LocalDateTime;

@Data
@TableName("research_report")
public class ResearchReport {

    @TableId(type = IdType.AUTO)
    private Long id;

    private String title;

    /** 作品说明：研报类别区分个股与行业。 */
    private String reportType;

    private String stockCode;

    private String stockName;

    private String orgName;

    private String orgSname;

    private LocalDate publishDate;

    private String industryName;

    private String rating;

    private String lastRating;

    private String researcher;

    private String predictThisYearEps;

    private String predictThisYearPe;

    private String aimPrice;

    private String datasetTag;

    /** 作品说明：本地是否有研报 PDF 原文 */
    private Integer pdfPresent;

    private LocalDateTime createdAt;

    private LocalDateTime updatedAt;
}
