CREATE TABLE research_report_material (
    report_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    source_key CHAR(64) NOT NULL,
    source_path VARCHAR(1024) NOT NULL,
    file_name VARCHAR(300) NOT NULL,
    sha256 CHAR(64) NOT NULL,
    ocr_sha256 CHAR(64) NOT NULL DEFAULT '',
    pages JSON NOT NULL,
    page_count INT NOT NULL DEFAULT 0,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_research_source (source_key),
    CONSTRAINT fk_research_material_report FOREIGN KEY (report_id) REFERENCES research_report(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
