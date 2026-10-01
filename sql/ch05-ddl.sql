-- Existing databases only: scripts/migrate_ch05_schema.py validates and skips completed steps.
ALTER TABLE tickets ADD COLUMN request_id VARCHAR(64) NULL COMMENT 'Ch05 confirmed offer idempotency key';
CREATE UNIQUE INDEX uk_tickets_request_id ON tickets (request_id);
