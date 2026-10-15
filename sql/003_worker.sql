ALTER TABLE audits
    ADD COLUMN started_at BIGINT UNSIGNED NULL,
    ADD COLUMN finished_at BIGINT UNSIGNED NULL,
    ADD COLUMN heartbeat_at BIGINT UNSIGNED NULL,
    ADD COLUMN worker_id CHAR(36) NULL,
    ADD COLUMN prompt_version VARCHAR(32) NULL,
    ADD COLUMN response_id VARCHAR(255) NULL,
    ADD COLUMN result_json LONGTEXT NULL,
    ADD COLUMN error_code VARCHAR(128) NULL,
    ADD INDEX idx_audits_recovery (job_status, heartbeat_at);
