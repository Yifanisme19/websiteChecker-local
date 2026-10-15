-- Run with ./manage local|prod tokens migrate. The command also adds audits.api_token_id.
CREATE TABLE IF NOT EXISTS api_tokens (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    note VARCHAR(1000) NOT NULL DEFAULT '',
    token_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    fingerprint CHAR(12) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    created_at BIGINT UNSIGNED NOT NULL,
    created_by VARCHAR(255) NOT NULL,
    last_used_at BIGINT UNSIGNED NULL,
    revoked_at BIGINT UNSIGNED NULL,
    revoked_by VARCHAR(255) NULL,
    UNIQUE INDEX uq_api_tokens_hash (token_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
