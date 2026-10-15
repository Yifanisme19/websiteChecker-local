-- Apply once to existing databases, after 004. Existing JSON is preserved.
ALTER TABLE audit_reports
    ADD COLUMN report_html LONGTEXT NULL,
    ADD COLUMN template_version VARCHAR(64) NULL;
