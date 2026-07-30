-- Decisions humaines sur un Versand bloque apres resultat fournisseur ambigu.
-- La decision est distincte des problemes d identification PDF.

SET NAMES utf8mb4;
SET time_zone = '+00:00';

CREATE TABLE IF NOT EXISTS versand_review_decision (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    versand_id BIGINT UNSIGNED NOT NULL,
    decision VARCHAR(40) NOT NULL,
    reason VARCHAR(1000) NOT NULL,
    decided_by VARCHAR(255) NOT NULL DEFAULT 'HOME_CLI',
    decided_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    CONSTRAINT uq_versand_review_decision
        UNIQUE (versand_id, decision),
    KEY ix_versand_review_decision_date (decision, decided_at),
    CONSTRAINT chk_versand_review_decision CHECK (
        decision IN (
            'PROVIDER_ACCEPTED',
            'PROVIDER_NOT_ACCEPTED_RETRY',
            'KEEP_UNKNOWN'
        )
    ),
    CONSTRAINT fk_versand_review_versand
        FOREIGN KEY (versand_id) REFERENCES versand (id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
) ENGINE = InnoDB
  DEFAULT CHARACTER SET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci;

INSERT IGNORE INTO schema_migration (version)
VALUES ('004_versand_review_decision');
