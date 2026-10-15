CREATE TABLE IF NOT EXISTS audit_reports (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    audit_id BIGINT UNSIGNED NOT NULL,
    schema_version VARCHAR(64) NOT NULL,
    report_json JSON NOT NULL,
    created_at BIGINT UNSIGNED NOT NULL,
    updated_at BIGINT UNSIGNED NOT NULL,
    deleted_at BIGINT UNSIGNED NULL DEFAULT NULL,
    UNIQUE INDEX uq_audit_reports_audit (audit_id),
    CONSTRAINT fk_audit_reports_audit FOREIGN KEY (audit_id) REFERENCES audits(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
