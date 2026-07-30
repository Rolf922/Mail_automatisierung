-- Prüfversand - schéma initial MySQL 8.4
-- Les dates applicatives sont écrites en UTC.

SET NAMES utf8mb4;
SET time_zone = '+00:00';

CREATE TABLE IF NOT EXISTS schema_migration (
    version VARCHAR(64) NOT NULL,
    applied_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (version)
) ENGINE = InnoDB
  DEFAULT CHARACTER SET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS baustelle (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    name VARCHAR(255) NOT NULL,
    kunde_name VARCHAR(255) NOT NULL,
    adresse VARCHAR(500) NULL,
    aktiv BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    KEY ix_baustelle_aktiv (aktiv)
) ENGINE = InnoDB
  DEFAULT CHARACTER SET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS auftrag (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    auftragsnummer VARCHAR(64) CHARACTER SET utf8mb4
        COLLATE utf8mb4_bin NOT NULL,
    baustelle_id BIGINT UNSIGNED NOT NULL,
    aktiv BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    CONSTRAINT uq_auftrag_auftragsnummer UNIQUE (auftragsnummer),
    KEY ix_auftrag_baustelle (baustelle_id),
    KEY ix_auftrag_aktiv (aktiv),
    CONSTRAINT fk_auftrag_baustelle
        FOREIGN KEY (baustelle_id) REFERENCES baustelle (id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
) ENGINE = InnoDB
  DEFAULT CHARACTER SET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS empfaenger (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    auftrag_id BIGINT UNSIGNED NOT NULL,
    kontakt_name VARCHAR(255) NULL,
    email VARCHAR(320) CHARACTER SET utf8mb4
        COLLATE utf8mb4_0900_ai_ci NOT NULL,
    empfaengertyp VARCHAR(8) NOT NULL,
    aktiv BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    CONSTRAINT chk_empfaenger_typ
        CHECK (empfaengertyp IN ('TO', 'CC')),
    CONSTRAINT uq_empfaenger_auftrag_email_typ
        UNIQUE (auftrag_id, email, empfaengertyp),
    KEY ix_empfaenger_aktiv (aktiv),
    CONSTRAINT fk_empfaenger_auftrag
        FOREIGN KEY (auftrag_id) REFERENCES auftrag (id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
) ENGINE = InnoDB
  DEFAULT CHARACTER SET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS csv_import (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    dateiname VARCHAR(255) NOT NULL,
    sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    status VARCHAR(32) NOT NULL,
    zeilen_gesamt INT UNSIGNED NOT NULL DEFAULT 0,
    eingefuegt INT UNSIGNED NOT NULL DEFAULT 0,
    aktualisiert INT UNSIGNED NOT NULL DEFAULT 0,
    abgelehnt INT UNSIGNED NOT NULL DEFAULT 0,
    fehlerbericht JSON NULL,
    imported_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    CONSTRAINT uq_csv_import_sha256 UNIQUE (sha256),
    CONSTRAINT chk_csv_import_status
        CHECK (status IN ('VALIDATED', 'IMPORTED', 'REJECTED', 'FAILED'))
) ENGINE = InnoDB
  DEFAULT CHARACTER SET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS protokoll (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    auftrag_id BIGINT UNSIGNED NULL,
    dateiname VARCHAR(255) NOT NULL,
    nas_pfad VARCHAR(1024) NOT NULL,
    sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    dateigroesse BIGINT UNSIGNED NOT NULL,
    datei_geaendert_at DATETIME(6) NOT NULL,
    first_seen_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    last_seen_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    stable_scan_count TINYINT UNSIGNED NOT NULL DEFAULT 1,
    status VARCHAR(32) NOT NULL,
    letzte_fehler TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    CONSTRAINT uq_protokoll_sha256 UNIQUE (sha256),
    KEY ix_protokoll_auftrag (auftrag_id),
    KEY ix_protokoll_status (status),
    CONSTRAINT chk_protokoll_status CHECK (
        status IN (
            'DISCOVERED',
            'READY',
            'SENDING',
            'ACCEPTED',
            'FAILED',
            'MANUAL_REVIEW',
            'QUARANTINED',
            'MANUALLY_SENT'
        )
    ),
    CONSTRAINT fk_protokoll_auftrag
        FOREIGN KEY (auftrag_id) REFERENCES auftrag (id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
) ENGINE = InnoDB
  DEFAULT CHARACTER SET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS versand (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    auftrag_id BIGINT UNSIGNED NOT NULL,
    correlation_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    dedupe_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    modus VARCHAR(16) NOT NULL,
    status VARCHAR(32) NOT NULL,
    mail_provider VARCHAR(16) NOT NULL,
    empfaenger_snapshot JSON NOT NULL,
    betreff VARCHAR(500) NOT NULL,
    body_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    period_start DATE NULL,
    planned_at DATETIME(6) NULL,
    sending_started_at DATETIME(6) NULL,
    accepted_at DATETIME(6) NULL,
    delivered_at DATETIME(6) NULL,
    versuch_anzahl SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    letzte_fehler TEXT NULL,
    manual_actor VARCHAR(255) NULL,
    manual_reason VARCHAR(1000) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    CONSTRAINT uq_versand_correlation UNIQUE (correlation_id),
    CONSTRAINT uq_versand_dedupe UNIQUE (dedupe_key),
    KEY ix_versand_auftrag (auftrag_id),
    KEY ix_versand_status_planned (status, planned_at),
    CONSTRAINT chk_versand_modus
        CHECK (modus IN ('AUTOMATIC', 'MANUAL')),
    CONSTRAINT chk_versand_status CHECK (
        status IN (
            'PLANNED',
            'SENDING',
            'ACCEPTED',
            'DELIVERED',
            'FAILED',
            'MANUAL_REVIEW',
            'MANUALLY_SENT'
        )
    ),
    CONSTRAINT chk_versand_provider
        CHECK (mail_provider IN ('mock', 'graph', 'smtp', 'manual')),
    CONSTRAINT fk_versand_auftrag
        FOREIGN KEY (auftrag_id) REFERENCES auftrag (id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
) ENGINE = InnoDB
  DEFAULT CHARACTER SET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS versand_protokoll (
    versand_id BIGINT UNSIGNED NOT NULL,
    protokoll_id BIGINT UNSIGNED NOT NULL,
    attached_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (versand_id, protokoll_id),
    CONSTRAINT uq_versand_protokoll_once UNIQUE (protokoll_id),
    CONSTRAINT fk_versand_protokoll_versand
        FOREIGN KEY (versand_id) REFERENCES versand (id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT fk_versand_protokoll_protokoll
        FOREIGN KEY (protokoll_id) REFERENCES protokoll (id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
) ENGINE = InnoDB
  DEFAULT CHARACTER SET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS audit_event (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    correlation_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    event_type VARCHAR(64) NOT NULL,
    entity_type VARCHAR(64) NULL,
    entity_id BIGINT UNSIGNED NULL,
    actor VARCHAR(255) NULL,
    details JSON NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    KEY ix_audit_created_at (created_at),
    KEY ix_audit_correlation (correlation_id),
    KEY ix_audit_entity (entity_type, entity_id)
) ENGINE = InnoDB
  DEFAULT CHARACTER SET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci;

INSERT IGNORE INTO schema_migration (version)
VALUES ('001_initial_schema');

