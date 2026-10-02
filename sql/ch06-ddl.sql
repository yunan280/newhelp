-- Additive migration only; existing messages keep NULL and are never rewritten.
ALTER TABLE messages ADD COLUMN ch06_event_key VARCHAR(64) NULL COMMENT 'Ch06 turn phase event idempotency';
CREATE UNIQUE INDEX uk_messages_ch06_event_key ON messages (ch06_event_key);
