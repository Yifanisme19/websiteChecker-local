CREATE TABLE IF NOT EXISTS discussion_notifications (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    audit_id BIGINT UNSIGNED NOT NULL,
    status ENUM('pending', 'unknown', 'sent', 'failed') NOT NULL DEFAULT 'pending',
    discussion_id VARCHAR(255) NULL,
    http_status SMALLINT UNSIGNED NULL,
    error_code VARCHAR(64) NULL,
    created_at BIGINT UNSIGNED NOT NULL,
    attempted_at BIGINT UNSIGNED NULL,
    finished_at BIGINT UNSIGNED NULL,
    UNIQUE KEY uq_discussion_audit (audit_id),
    KEY ix_discussion_pending (status, id),
    CONSTRAINT fk_discussion_audit FOREIGN KEY (audit_id) REFERENCES audits(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
