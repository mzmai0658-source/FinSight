-- M4~M6 平台扩展：ETL 任务状态机 / 研报库 / 自选关注 / 站内信 / AI 诊股报告

CREATE TABLE etl_task (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    task_uid    CHAR(36)        NOT NULL COMMENT '对外暴露的任务 UUID',
    file_name   VARCHAR(255)    NOT NULL COMMENT '原始文件名',
    file_path   VARCHAR(500)    NOT NULL COMMENT '落盘路径（uploads 目录）',
    file_type   VARCHAR(16)     NOT NULL DEFAULT 'financial' COMMENT 'financial=财报 research=研报',
    status      VARCHAR(16)     NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/RUNNING/SUCCESS/FAILED',
    stock_code  VARCHAR(20)     NOT NULL DEFAULT '' COMMENT '解析出的股票代码',
    report_year INT             NULL COMMENT '解析出的报告年份',
    message     VARCHAR(2000)   NOT NULL DEFAULT '' COMMENT '结果摘要 / 失败原因',
    steps_json  JSON            NULL COMMENT '管线步骤明细（Python 返回）',
    created_by  BIGINT UNSIGNED NOT NULL COMMENT '上传管理员 userId',
    started_at  DATETIME        NULL,
    finished_at DATETIME        NULL,
    created_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_task_uid (task_uid),
    KEY idx_status_created (status, created_at DESC),
    KEY idx_created (created_at DESC)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT 'ETL 任务状态机（管理端上传）';

CREATE TABLE research_report (
    id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    title         VARCHAR(300)    NOT NULL COMMENT '研报标题（即 PDF 文件名）',
    report_type   VARCHAR(16)     NOT NULL DEFAULT 'stock' COMMENT 'stock=个股 industry=行业',
    stock_code    VARCHAR(20)     NOT NULL DEFAULT '' COMMENT '个股研报关联股票代码',
    stock_name    VARCHAR(50)     NOT NULL DEFAULT '',
    org_name      VARCHAR(120)    NOT NULL DEFAULT '' COMMENT '发布机构全称',
    org_sname     VARCHAR(60)     NOT NULL DEFAULT '' COMMENT '机构简称',
    publish_date  DATE            NULL,
    industry_name VARCHAR(80)     NOT NULL DEFAULT '',
    rating        VARCHAR(30)     NOT NULL DEFAULT '' COMMENT '东财评级（买入/增持…）',
    last_rating   VARCHAR(30)     NOT NULL DEFAULT '' COMMENT '上期评级',
    researcher    VARCHAR(120)    NOT NULL DEFAULT '',
    predict_this_year_eps VARCHAR(20) NOT NULL DEFAULT '' COMMENT '预测当年 EPS',
    predict_this_year_pe  VARCHAR(20) NOT NULL DEFAULT '' COMMENT '预测当年 PE',
    aim_price     VARCHAR(20)     NOT NULL DEFAULT '' COMMENT '目标价',
    dataset_tag   VARCHAR(20)     NOT NULL DEFAULT '' COMMENT '医药 / 中药',
    pdf_present   TINYINT         NOT NULL DEFAULT 0 COMMENT '本地是否存在研报 PDF',
    created_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_report (title(180), org_name(60), publish_date),
    KEY idx_stock_date (stock_code, publish_date DESC),
    KEY idx_type_date (report_type, publish_date DESC)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT '研究报告元数据库';

CREATE TABLE watchlist (
    id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id    BIGINT UNSIGNED NOT NULL,
    stock_code VARCHAR(20)     NOT NULL,
    created_at DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_user_stock (user_id, stock_code),
    KEY idx_user (user_id, created_at DESC)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT '用户自选股';

CREATE TABLE notification (
    id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id    BIGINT UNSIGNED NOT NULL,
    type       VARCHAR(32)     NOT NULL DEFAULT 'system' COMMENT 'system / advisor_report / etl',
    title      VARCHAR(200)    NOT NULL DEFAULT '',
    content    VARCHAR(2000)   NOT NULL DEFAULT '',
    link       VARCHAR(300)    NOT NULL DEFAULT '' COMMENT '前端跳转路径',
    read_flag  TINYINT         NOT NULL DEFAULT 0,
    created_at DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_user_read (user_id, read_flag, id DESC)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT '站内信';

CREATE TABLE advisor_report (
    id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id      BIGINT UNSIGNED NOT NULL,
    stock_code   VARCHAR(20)     NOT NULL,
    stock_name   VARCHAR(50)     NOT NULL DEFAULT '',
    risk_profile VARCHAR(16)     NOT NULL DEFAULT 'balanced',
    score        INT             NOT NULL DEFAULT 0 COMMENT '规则引擎总分 0-100',
    rating       VARCHAR(16)     NOT NULL DEFAULT '' COMMENT '优秀/良好/中性/谨慎/高风险',
    dimensions   JSON            NULL COMMENT '分维度评分明细',
    report_md    MEDIUMTEXT      NULL COMMENT 'LLM 生成的诊断报告 Markdown',
    status       VARCHAR(16)     NOT NULL DEFAULT 'GENERATING' COMMENT 'GENERATING/READY/FAILED',
    created_at   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_user_stock (user_id, stock_code, id DESC)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT 'AI 诊股报告';
