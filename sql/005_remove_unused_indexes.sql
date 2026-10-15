-- Existing databases upgraded from the original 001/002/004 definitions.
-- Apply once. Fresh databases using current 001 + 004 must skip this migration.
ALTER TABLE audits
    DROP INDEX idx_audits_project,
    DROP INDEX idx_audits_company;
ALTER TABLE audit_reports
    DROP INDEX idx_audit_reports_deleted_created;
