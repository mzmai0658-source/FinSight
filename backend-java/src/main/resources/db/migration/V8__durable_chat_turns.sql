CREATE TABLE chat_turn (
    task_id CHAR(36) NOT NULL,
    user_id BIGINT UNSIGNED NOT NULL,
    session_id BIGINT UNSIGNED NOT NULL,
    session_uid CHAR(36) NOT NULL,
    client_request_id VARCHAR(128) NOT NULL,
    contract_version INT NOT NULL DEFAULT 3,
    question TEXT NOT NULL,
    status VARCHAR(16) NOT NULL,
    deadline_ms BIGINT NOT NULL,
    result JSON NULL,
    saved TINYINT NOT NULL DEFAULT 0,
    error_code VARCHAR(80) NULL,
    active_slot TINYINT GENERATED ALWAYS AS
        (CASE WHEN status IN ('queued','running','cancelling') THEN 1 ELSE NULL END) STORED,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (task_id),
    UNIQUE KEY uk_turn_client (user_id, client_request_id),
    UNIQUE KEY uk_session_active_turn (session_id, active_slot),
    KEY idx_turn_session (session_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
