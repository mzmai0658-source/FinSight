CREATE TABLE mq_outbox_message (
    id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    message_uid   CHAR(36)        NOT NULL,
    exchange_name VARCHAR(120)    NOT NULL,
    routing_key   VARCHAR(120)    NOT NULL,
    payload_json  JSON            NOT NULL,
    payload_type  VARCHAR(300)    NOT NULL,
    status        VARCHAR(16)     NOT NULL DEFAULT 'PENDING',
    retry_count   INT             NOT NULL DEFAULT 0,
    last_error    VARCHAR(1000)   NOT NULL DEFAULT '',
    next_retry_at DATETIME        NOT NULL,
    sent_at       DATETIME        NULL,
    created_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_message_uid (message_uid),
    KEY idx_status_retry (status, next_retry_at, retry_count),
    KEY idx_created (created_at DESC)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT 'RabbitMQ reliable publish outbox';
