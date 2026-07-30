-- Pruefversand - etat courant des observations du scanner.
-- Le chemin canonique est identifie par SHA-256 pour garder une cle courte.

SET NAMES utf8mb4;
SET time_zone = '+00:00';

CREATE TABLE IF NOT EXISTS scan_observation (
    path_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    nas_pfad VARCHAR(1024) NOT NULL,
    sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    dateigroesse BIGINT UNSIGNED NOT NULL,
    datei_geaendert_ns BIGINT UNSIGNED NOT NULL,
    stable_scan_count TINYINT UNSIGNED NOT NULL DEFAULT 1,
    status VARCHAR(32) NOT NULL,
    letzte_fehler TEXT NULL,
    first_seen_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    last_seen_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (path_sha256),
    KEY ix_scan_observation_status (status),
    KEY ix_scan_observation_sha256 (sha256),
    CONSTRAINT chk_scan_observation_status CHECK (
        status IN (
            'DISCOVERED',
            'READY',
            'MANUAL_REVIEW',
            'QUARANTINED'
        )
    )
) ENGINE = InnoDB
  DEFAULT CHARACTER SET = utf8mb4
  COLLATE = utf8mb4_0900_ai_ci;

INSERT IGNORE INTO schema_migration (version)
VALUES ('002_scan_observation');
