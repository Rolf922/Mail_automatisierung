from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from pruefversand.config import Settings
from pruefversand.database import apply_migration
from pruefversand.pdf_tools import calculate_sha256


class ManualReviewError(ValueError):
    pass


class ManualReviewSafetyError(ManualReviewError):
    pass


@dataclass(frozen=True, slots=True)
class ManualReviewItem:
    protokoll_id: int
    filename: str
    reason: str
    sha256: str
    stable_scan_count: int
    nas_path: Path


@dataclass(frozen=True, slots=True)
class ManualReviewDecisionResult:
    decision_id: int
    protokoll_id: int
    action: str
    filename: str
    reason: str
    auftragsnummer: str | None
    protokoll_status: str
    decided_at: datetime


def assert_manual_review_safety(settings: Settings) -> None:
    if settings.app.environment not in {'home', 'test'}:
        raise ManualReviewSafetyError(
            'manual-review exige un environnement HOME ou TEST'
        )
    if not settings.app.dry_run:
        raise ManualReviewSafetyError(
            'manual-review exige dry_run = true'
        )
    if settings.mail.provider != 'mock':
        raise ManualReviewSafetyError(
            'manual-review exige mail.provider = mock'
        )
    if settings.mail.allowed_recipient_domains != ('example.invalid',):
        raise ManualReviewSafetyError(
            'manual-review autorise uniquement example.invalid'
        )
    if settings.app.environment == 'home':
        if settings.database.host not in {'127.0.0.1', 'localhost', '::1'}:
            raise ManualReviewSafetyError(
                'manual-review exige MySQL local en HOME'
            )
        if settings.database.port != 3307:
            raise ManualReviewSafetyError(
                'manual-review exige le port MySQL 3307 en HOME'
            )
        expected_nas = (
            settings.project_root / 'sample_data/nas_mock'
        ).resolve()
        if settings.app.nas_path.resolve() != expected_nas:
            raise ManualReviewSafetyError(
                'manual-review exige sample_data/nas_mock en HOME'
            )


def apply_manual_review_migrations(
    connection: Any,
    settings: Settings,
) -> None:
    assert_manual_review_safety(settings)
    for migration_name in (
        '001_initial_schema.sql',
        '002_scan_observation.sql',
        '003_manual_review_decision.sql',
        '004_versand_review_decision.sql',
    ):
        apply_migration(
            connection,
            settings.project_root / 'sql/migrations' / migration_name,
        )


def _normalized_reason(reason: str) -> str:
    normalized = reason.strip()
    if not normalized:
        raise ManualReviewError('une raison non vide est obligatoire')
    if len(normalized) > 1000:
        raise ManualReviewError('la raison depasse 1000 caracteres')
    return normalized


def _normalized_auftragsnummer(auftragsnummer: str) -> str:
    normalized = auftragsnummer.strip()
    if not normalized:
        raise ManualReviewError('Auftragsnummer vide')
    if len(normalized) > 64 or not normalized.isalnum():
        raise ManualReviewError('Auftragsnummer non conforme')
    return normalized


def list_manual_reviews(
    connection: Any,
    settings: Settings,
) -> tuple[ManualReviewItem, ...]:
    assert_manual_review_safety(settings)
    cursor = connection.cursor()
    try:
        cursor.execute(
            '''
            SELECT
                p.id,
                p.dateiname,
                COALESCE(p.letzte_fehler, ''),
                p.sha256,
                p.stable_scan_count,
                p.nas_pfad
            FROM protokoll AS p
            LEFT JOIN manual_review_decision AS d
                ON d.protokoll_id = p.id
            WHERE p.status = 'MANUAL_REVIEW'
              AND d.id IS NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM versand_protokoll AS vp
                  JOIN versand AS v ON v.id = vp.versand_id
                  WHERE vp.protokoll_id = p.id
                    AND v.status = 'MANUAL_REVIEW'
              )
            ORDER BY p.first_seen_at, p.id
            '''
        )
        items = tuple(
            ManualReviewItem(
                protokoll_id=int(row[0]),
                filename=str(row[1]),
                reason=str(row[2]),
                sha256=str(row[3]),
                stable_scan_count=int(row[4]),
                nas_path=Path(str(row[5])),
            )
            for row in cursor.fetchall()
        )
        connection.commit()
        return items
    finally:
        cursor.close()


def _lock_review_item(
    cursor: Any,
    settings: Settings,
    protokoll_id: int,
) -> tuple[str, str, str, Path]:
    if protokoll_id <= 0:
        raise ManualReviewError('identifiant Pruefprotokoll invalide')
    cursor.execute(
        '''
        SELECT dateiname, status, sha256, nas_pfad, stable_scan_count
        FROM protokoll
        WHERE id = %s
        FOR UPDATE
        ''',
        (protokoll_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise ManualReviewError('Pruefprotokoll introuvable')
    filename = str(row[0])
    if str(row[1]) != 'MANUAL_REVIEW':
        raise ManualReviewError(
            'le Pruefprotokoll n est plus en MANUAL_REVIEW'
        )
    sha256 = str(row[2])
    nas_path = Path(str(row[3]))
    if int(row[4]) < 2:
        raise ManualReviewError(
            'deux scans stables sont requis avant une decision'
        )

    cursor.execute(
        '''
        SELECT stable_scan_count, sha256
        FROM scan_observation
        WHERE nas_pfad = %s
        FOR UPDATE
        ''',
        (str(nas_path.resolve()),),
    )
    observation = cursor.fetchone()
    if (
        observation is None
        or int(observation[0]) < 2
        or str(observation[1]) != sha256
    ):
        raise ManualReviewError(
            'observation stable ou SHA-256 MySQL incoherent'
        )

    cursor.execute(
        '''
        SELECT id
        FROM manual_review_decision
        WHERE protokoll_id = %s
        FOR UPDATE
        ''',
        (protokoll_id,),
    )
    if cursor.fetchone() is not None:
        raise ManualReviewError(
            'une decision existe deja pour ce Pruefprotokoll'
        )

    resolved_path = nas_path.resolve()
    try:
        resolved_path.relative_to(settings.app.nas_path.resolve())
    except ValueError as exc:
        raise ManualReviewError(
            'le Pruefprotokoll est hors du NAS HOME autorise'
        ) from exc
    if not resolved_path.is_file():
        raise ManualReviewError('le fichier PDF NAS est introuvable')
    if calculate_sha256(resolved_path) != sha256:
        raise ManualReviewError(
            'le fichier PDF a change depuis le dernier scan'
        )
    return filename, sha256, str(resolved_path), resolved_path


def _record_audit(
    cursor: Any,
    *,
    protokoll_id: int,
    action: str,
    reason: str,
    sha256: str,
    auftragsnummer: str | None,
) -> None:
    cursor.execute(
        '''
        INSERT INTO audit_event
            (event_type, entity_type, entity_id, actor, details)
        VALUES (%s, 'protokoll', %s, 'HOME_CLI', %s)
        ''',
        (
            f'MANUAL_REVIEW_{action}',
            protokoll_id,
            json.dumps(
                {
                    'action': action,
                    'reason': reason,
                    'sha256': sha256,
                    'auftragsnummer': auftragsnummer,
                },
                ensure_ascii=False,
            ),
        ),
    )


def assign_manual_review(
    connection: Any,
    settings: Settings,
    protokoll_id: int,
    auftragsnummer: str,
    reason: str,
) -> ManualReviewDecisionResult:
    assert_manual_review_safety(settings)
    normalized_auftrag = _normalized_auftragsnummer(auftragsnummer)
    normalized_reason = _normalized_reason(reason)
    connection.start_transaction()
    cursor = connection.cursor()
    try:
        filename, sha256, canonical_path, _ = _lock_review_item(
            cursor,
            settings,
            protokoll_id,
        )
        cursor.execute(
            '''
            SELECT id, auftragsnummer
            FROM auftrag
            WHERE auftragsnummer = %s AND aktiv = TRUE
            FOR UPDATE
            ''',
            (normalized_auftrag,),
        )
        auftrag = cursor.fetchone()
        if auftrag is None:
            raise ManualReviewError('Auftrag inexistant ou inactif')

        cursor.execute(
            '''
            INSERT INTO manual_review_decision
                (
                    protokoll_id,
                    action,
                    assigned_auftrag_id,
                    reason,
                    decided_by
                )
            VALUES (%s, 'ASSIGN', %s, %s, 'HOME_CLI')
            ''',
            (protokoll_id, auftrag[0], normalized_reason),
        )
        decision_id = int(cursor.lastrowid)
        cursor.execute(
            '''
            UPDATE protokoll
            SET auftrag_id = %s,
                status = 'READY',
                letzte_fehler = NULL
            WHERE id = %s
            ''',
            (auftrag[0], protokoll_id),
        )
        cursor.execute(
            '''
            UPDATE scan_observation
            SET status = 'READY', letzte_fehler = NULL
            WHERE nas_pfad = %s AND sha256 = %s
            ''',
            (canonical_path, sha256),
        )
        _record_audit(
            cursor,
            protokoll_id=protokoll_id,
            action='ASSIGN',
            reason=normalized_reason,
            sha256=sha256,
            auftragsnummer=str(auftrag[1]),
        )
        cursor.execute(
            '''
            SELECT decided_at
            FROM manual_review_decision
            WHERE id = %s
            ''',
            (decision_id,),
        )
        decided_at = cursor.fetchone()[0]
        connection.commit()
        return ManualReviewDecisionResult(
            decision_id=decision_id,
            protokoll_id=protokoll_id,
            action='ASSIGN',
            filename=filename,
            reason=normalized_reason,
            auftragsnummer=str(auftrag[1]),
            protokoll_status='READY',
            decided_at=decided_at,
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


def ignore_manual_review(
    connection: Any,
    settings: Settings,
    protokoll_id: int,
    reason: str,
) -> ManualReviewDecisionResult:
    assert_manual_review_safety(settings)
    normalized_reason = _normalized_reason(reason)
    connection.start_transaction()
    cursor = connection.cursor()
    try:
        filename, sha256, _, resolved_path = _lock_review_item(
            cursor,
            settings,
            protokoll_id,
        )
        cursor.execute(
            '''
            INSERT INTO manual_review_decision
                (protokoll_id, action, reason, decided_by)
            VALUES (%s, 'IGNORE', %s, 'HOME_CLI')
            ''',
            (protokoll_id, normalized_reason),
        )
        decision_id = int(cursor.lastrowid)
        cursor.execute(
            '''
            UPDATE protokoll
            SET letzte_fehler = %s
            WHERE id = %s
            ''',
            (f'Ignore manuellement : {normalized_reason}', protokoll_id),
        )
        _record_audit(
            cursor,
            protokoll_id=protokoll_id,
            action='IGNORE',
            reason=normalized_reason,
            sha256=sha256,
            auftragsnummer=None,
        )
        cursor.execute(
            '''
            SELECT decided_at
            FROM manual_review_decision
            WHERE id = %s
            ''',
            (decision_id,),
        )
        decided_at = cursor.fetchone()[0]
        if not resolved_path.is_file():
            raise ManualReviewError(
                'le fichier PDF a disparu avant la decision'
            )
        connection.commit()
        return ManualReviewDecisionResult(
            decision_id=decision_id,
            protokoll_id=protokoll_id,
            action='IGNORE',
            filename=filename,
            reason=normalized_reason,
            auftragsnummer=None,
            protokoll_status='MANUAL_REVIEW',
            decided_at=decided_at,
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
