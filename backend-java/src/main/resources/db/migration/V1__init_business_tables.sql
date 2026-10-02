-- 业务基础表：用户 / 会话 / 消息 / 对话审计
-- 注：库内已有 ETL 维护的财务数据表（balance_sheet / income_sheet / cash_flow_sheet /
-- core_performance_indicators_sheet），由 Python ETL 负责，不纳入 Flyway 管理。

CREATE TABLE sys_user (
    id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    username      VARCHAR(64)     NOT NULL COMMENT '登录名',
    password_hash VARCHAR(100)    NOT NULL COMMENT 'BCrypt 哈希',
    nickname      VARCHAR(64)     NOT NULL DEFAULT '' COMMENT '昵称',
    role          VARCHAR(16)     NOT NULL DEFAULT 'USER' COMMENT 'USER / ADMIN',
    risk_profile  VARCHAR(16)     NOT NULL DEFAULT 'balanced' COMMENT '风险偏好: conservative/balanced/aggressive',
    status        TINYINT         NOT NULL DEFAULT 1 COMMENT '1 正常 0 禁用',
    deleted       TINYINT         NOT NULL DEFAULT 0,
    created_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_username (username)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT '用户';

CREATE TABLE chat_session (
    id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    session_uid   CHAR(36)        NOT NULL COMMENT '对外暴露的会话 UUID',
    user_id       BIGINT UNSIGNED NOT NULL,
    title         VARCHAR(120)    NOT NULL DEFAULT '新对话',
    message_count INT             NOT NULL DEFAULT 0,
    deleted       TINYINT         NOT NULL DEFAULT 0,
    created_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_session_uid (session_uid),
    KEY idx_user_updated (user_id, updated_at DESC)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT '对话会话';

CREATE TABLE chat_message (
    id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    session_id BIGINT UNSIGNED NOT NULL,
    role       VARCHAR(16)     NOT NULL COMMENT 'user / assistant',
    content    MEDIUMTEXT      NOT NULL,
    metadata   JSON            NULL COMMENT 'assistant 元数据: sql/图表/引用/执行过程等',
    created_at DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_session (session_id, id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT '对话消息';

CREATE TABLE chat_log (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id     BIGINT UNSIGNED NOT NULL,
    session_uid CHAR(36)        NOT NULL DEFAULT '',
    request_id  VARCHAR(64)     NOT NULL DEFAULT '' COMMENT '全链路 traceId',
    question    VARCHAR(1000)   NOT NULL DEFAULT '',
    status      VARCHAR(16)     NOT NULL DEFAULT 'ok' COMMENT 'ok / error / interrupted',
    duration_ms INT             NOT NULL DEFAULT 0,
    created_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_user_created (user_id, created_at DESC),
    KEY idx_created (created_at)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT '对话审计日志（MQ 异步写入）';
