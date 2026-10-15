-- Existing installations only. Apply once; new installations use 001 instead.
ALTER TABLE audits
    DROP PRIMARY KEY,
    ADD COLUMN id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY FIRST,
    ADD COLUMN updated_at BIGINT UNSIGNED NOT NULL DEFAULT 0 AFTER created_at,
    ADD COLUMN deleted_at BIGINT UNSIGNED NULL DEFAULT NULL AFTER updated_at,
    ADD UNIQUE INDEX uq_audits_job_id (job_id),
    DROP INDEX idx_audits_queue,
    ADD INDEX idx_audits_queue (job_status, deleted_at, created_at),
    ADD INDEX idx_audits_project (project_id, deleted_at, created_at),
    ADD INDEX idx_audits_company (company_id, deleted_at, created_at);

UPDATE audits SET updated_at = created_at WHERE updated_at = 0;
ALTER TABLE audits ALTER COLUMN updated_at DROP DEFAULT;
