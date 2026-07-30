-- Decisions humaines sur les Pruefprotokolle bloques en MANUAL_REVIEW.
-- Le fichier NAS reste intact et cette table conserve la decision et sa raison.

SET NAMES utf8mb4;
SET time_zone = '+00:00';

CREATE TABLE IF NOT EXISTS manual_review_decision (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    protokoll_id BIGINT UNSIGNED NOT NULL,
    action VARCHAR(16) NOT NULL,
    assigned_auftrag_id BIGINT UNSIGNED NULL,
    reason VARCHAR(1000) NOT NULL,
    decided_by VARCHAR(255) NOT NULL DEFAULT 'HOME_CLI',
    decided_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    CONSTRAINT uq_manual_review_protokoll UNIQUE (protokoll_id),
    KEY ix_manual_review_action_date (action, decided_at),
    KEY ix_manual_review_auftrag (assigned_auftrag_id),
    CONSTRAINT chk_manual_review_action
        CHECK (action IN ('ASSIGN', 'IGNORE')),
    CONSTRAINT chk_manual_review_target CHECK (
        (action = 'ASSIGN' AND assigned_auftrag_id IS NOT NULL)
        OR (action = 'IGNORE' AND assigned_auftrag_id IS NULL)
    ),
    CONSTRAINT fk_manual_review_protokoll
        FOREIGN KEY (protokoll_id) REFERENCES protokoll (id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT fk_manual_review_auftrag
        FOREIGN KEY (assigned_auftrag_id) REFERENCES auftrag (id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
) ENGINE = InnoDB
  DEFAULT CHARACTER SET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci;

INSERT IGNORE INTO schema_migration (version)
VALUES ('003_manual_review_decision');
