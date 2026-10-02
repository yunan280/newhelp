-- Additive migration only; existing messages keep NULL and are never rewritten.
ALTER TABLE messages ADD COLUMN ch06_event_key VARCHAR(64) NULL COMMENT 'Ch06 turn phase event idempotency';
CREATE UNIQUE INDEX uk_messages_ch06_event_key ON messages (ch06_event_key);

CREATE TABLE refund_applications (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  application_no VARCHAR(32) NOT NULL,
  offer_id VARCHAR(64) NOT NULL,
  conversation_id BIGINT UNSIGNED NOT NULL,
  user_id VARCHAR(64) NOT NULL,
  order_id VARCHAR(64) NOT NULL,
  reason VARCHAR(32) NOT NULL,
  order_snapshot JSON NOT NULL,
  assessment_snapshot JSON NOT NULL,
  policy_snapshot JSON NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'pending',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_refund_applications_offer_id (offer_id),
  UNIQUE KEY uk_refund_applications_application_no (application_no),
  KEY idx_refund_applications_conversation (conversation_id),
  CONSTRAINT fk_refund_applications_conversation FOREIGN KEY (conversation_id) REFERENCES conversations (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
