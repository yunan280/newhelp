-- Ch04 incremental schema. App writes created_at in UTC explicitly.
SET NAMES utf8mb4;

ALTER TABLE knowledge_chunks ADD COLUMN product_category VARCHAR(128) NULL;
ALTER TABLE messages ADD COLUMN citations JSON NULL;

CREATE TABLE low_confidence_questions (
  id                     BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  original_question      TEXT NOT NULL,
  source_conversation_id BIGINT UNSIGNED NULL,
  entry_point            ENUM('chat_stream','agent','cli') NOT NULL,
  trigger_stage          ENUM('retrieval','generation') NOT NULL,
  reason_code            VARCHAR(64) NOT NULL,
  reason                 TEXT NOT NULL,
  created_at             DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_low_confidence_conversation (source_conversation_id),
  KEY idx_low_confidence_created_at (created_at),
  CONSTRAINT fk_low_confidence_conversation FOREIGN KEY (source_conversation_id)
    REFERENCES conversations (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
