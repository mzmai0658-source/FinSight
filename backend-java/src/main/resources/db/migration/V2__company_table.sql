-- 上市公司主数据：取代注册表 xlsx + 代码内硬编码兜底，成为公司体系唯一事实来源
CREATE TABLE IF NOT EXISTS company (
    id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    stock_code    VARCHAR(20)  NOT NULL COMMENT '股票代码（6 位）',
    abbr          VARCHAR(50)  NOT NULL COMMENT 'A股简称',
    full_name     VARCHAR(120) NOT NULL DEFAULT '' COMMENT '公司全称',
    en_name       VARCHAR(200) NOT NULL DEFAULT '' COMMENT '英文名称',
    exchange      VARCHAR(20)  NOT NULL DEFAULT '' COMMENT '上市交易所（上交所/深交所）',
    board         VARCHAR(40)  NOT NULL DEFAULT '' COMMENT '证券类别/板块',
    industry      VARCHAR(80)  NOT NULL DEFAULT '' COMMENT '所属证监会行业',
    region        VARCHAR(60)  NOT NULL DEFAULT '' COMMENT '注册区域',
    reg_capital   VARCHAR(40)  NOT NULL DEFAULT '' COMMENT '注册资本（原始文本）',
    employees     INT          NOT NULL DEFAULT 0 COMMENT '雇员人数',
    dataset_tag   VARCHAR(20)  NOT NULL DEFAULT '' COMMENT '数据集标记（医药/中药）',
    data_status   VARCHAR(16)  NOT NULL DEFAULT 'pending' COMMENT 'imported=财务数据已入库 pending=待导入',
    first_year    INT          NULL COMMENT '已入库最早报告年份',
    last_year     INT          NULL COMMENT '已入库最新报告年份',
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_stock_code (stock_code),
    KEY idx_data_status (data_status),
    KEY idx_industry (industry)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '上市公司主数据';
