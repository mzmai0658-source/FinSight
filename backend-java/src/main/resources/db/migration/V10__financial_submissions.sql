-- Candidate processing does not grant approval or publication. Legacy tasks have
-- no candidate receipt and stay PROCESSING until explicitly reprocessed.
ALTER TABLE etl_task
    ADD COLUMN review_status VARCHAR(16) NULL,
    ADD COLUMN candidate_id CHAR(64) NULL,
    ADD COLUMN source_sha256 CHAR(64) NULL,
    ADD COLUMN company VARCHAR(255) NULL,
    ADD COLUMN report_period VARCHAR(8) NULL,
    ADD COLUMN published_version VARCHAR(255) NULL,
    ADD COLUMN publication_receipt_json JSON NULL,
    ADD KEY idx_submission_owner (created_by, file_type, id),
    ADD KEY idx_submission_review (file_type, review_status, id),
    ADD KEY idx_submission_candidate (candidate_id);

UPDATE etl_task SET review_status = 'PROCESSING' WHERE file_type = 'financial';

-- A database row lock serializes admin mutations across Java instances while
-- Python independently protects the canonical candidate and publication pointer.
CREATE TABLE financial_submission_operation_lock (
    id TINYINT UNSIGNED NOT NULL,
    PRIMARY KEY (id)
) ENGINE = InnoDB;

INSERT INTO financial_submission_operation_lock (id) VALUES (1);
