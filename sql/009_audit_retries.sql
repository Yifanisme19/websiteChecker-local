-- Apply to the target environment with an administrator before enabling the retry API.
CREATE TABLE IF NOT EXISTS audit_retries (
    original_audit_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    retry_audit_id BIGINT UNSIGNED NOT NULL,
    created_at BIGINT UNSIGNED NOT NULL,
    UNIQUE KEY uq_audit_retry_child (retry_audit_id),
    CONSTRAINT fk_retry_original FOREIGN KEY (original_audit_id) REFERENCES audits(id),
    CONSTRAINT fk_retry_child FOREIGN KEY (retry_audit_id) REFERENCES audits(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
