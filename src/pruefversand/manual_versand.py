from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any
import uuid

from pruefversand.config import Settings
from pruefversand.pdf_tools import calculate_sha256


class ManualVersandError(ValueError):
    pass


class ManualVersandSafetyError(ManualVersandError):
    pass


@dataclass(frozen=True, slots=True)
class ManualVersandResult:
    versand_id: int
    protokoll_id: int
    filename: str
    sha256: str
    auftragsnummer: str
    operator: str
    reason: str
    sent_at: datetime
    recipients: tuple[str, ...]
    protokoll_status: str


def assert_manual_versand_safety(settings: Settings) -> None:
    if settings.app.environment not in {'home', 'test'}:
        raise ManualVersandSafetyError(
            'manual-versand exige un environnement HOME ou TEST'
        )
    if not settings.app.dry_run:
        raise ManualVersandSafetyError(
            'manual-versand exige dry_run = true'
        )
    if settings.mail.provider != 'mock':
        raise ManualVersandSafetyError(
            'manual-versand exige mail.provider = mock'
        )
    if settings.mail.allowed_recipient_domains != ('example.invalid',):
        raise ManualVersandSafetyError(
            'manual-versand autorise uniquement example.invalid'
        )
    if settings.app.environment == 'home':
        if settings.database.host not in {'127.0.0.1', 'localhost', '::1'}:
            raise ManualVersandSafetyError(
                'manual-versand exige MySQL local en HOME'
            )
        if settings.database.port != 3307:
            raise ManualVersandSafetyError(
                'manual-versand exige le port MySQL 3307 en HOME'
            )
        expected_nas = (
            settings.project_root / 'sample_data/nas_mock'
        ).resolve()
        if settings.app.nas_path.resolve() != expected_nas:
            raise ManualVersandSafetyError(
                'manual-versand exige sample_data/nas_mock en HOME'
            )


def _normalized(value: str, label: str, maximum: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise ManualVersandError(f'{label} obligatoire')
    if len(normalized) > maximum:
        raise ManualVersandError(f'{label} depasse {maximum} caracteres')
    return normalized


def _assert_example_invalid(address: str) -> None:
    if address.count('@') != 1:
        raise ManualVersandError('Empfaenger invalide')
    if address.rsplit('@', maxsplit=1)[1].lower() != 'example.invalid':
        raise ManualVersandError(
            'manual-versand autorise uniquement example.invalid'
        )


def record_manual_urgent_versand(
    connection: Any,
    settings: Settings,
    protokoll_id: int,
    operator: str,
    reason: str,
    *,
    sent_at: datetime | None = None,
) -> ManualVersandResult:
    assert_manual_versand_safety(settings)
    if protokoll_id <= 0:
        raise ManualVersandError('identifiant Pruefprotokoll invalide')
    normalized_operator = _normalized(operator, 'operateur', 255)
    normalized_reason = _normalized(reason, 'raison', 1000)
    event_at = sent_at or datetime.now(timezone.utc)
    if event_at.tzinfo is None:
        raise ManualVersandError('la date manuelle exige un fuseau horaire')
    mysql_event_at = event_at.astimezone(timezone.utc).replace(tzinfo=None)

    connection.start_transaction()
    cursor = connection.cursor()
    try:
        cursor.execute(
            '''
            SELECT
                p.dateiname,
                p.status,
                p.sha256,
                p.nas_pfad,
                p.dateigroesse,
                p.stable_scan_count,
                a.id,
                a.auftragsnummer
            FROM protokoll AS p
            JOIN auftrag AS a ON a.id = p.auftrag_id
            WHERE p.id = %s AND a.aktiv = TRUE
            FOR UPDATE
            ''',
            (protokoll_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise ManualVersandError(
                'Pruefprotokoll ou Auftrag actif introuvable'
            )
        if str(row[1]) != 'READY':
            raise ManualVersandError(
                'le Pruefprotokoll n est pas READY'
            )
        if int(row[5]) < 2:
            raise ManualVersandError(
                'deux scans stables sont requis'
            )
        cursor.execute(
            '''
            SELECT versand_id
            FROM versand_protokoll
            WHERE protokoll_id = %s
            FOR UPDATE
            ''',
            (protokoll_id,),
        )
        if cursor.fetchone() is not None:
            raise ManualVersandError(
                'le Pruefprotokoll est deja lie a un Versand'
            )

        path = Path(str(row[3])).resolve()
        try:
            path.relative_to(settings.app.nas_path.resolve())
        except ValueError as exc:
            raise ManualVersandError(
                'le Pruefprotokoll est hors du NAS HOME autorise'
            ) from exc
        if not path.is_file():
            raise ManualVersandError('le fichier PDF NAS est introuvable')
        expected_sha256 = str(row[2])
        if calculate_sha256(path) != expected_sha256:
            raise ManualVersandError(
                'le SHA-256 a change depuis le dernier scan'
            )
        if path.stat().st_size != int(row[4]):
            raise ManualVersandError(
                'la taille a change depuis le dernier scan'
            )
        cursor.execute(
            '''
            SELECT stable_scan_count, sha256
            FROM scan_observation
            WHERE nas_pfad = %s
            FOR UPDATE
            ''',
            (str(path),),
        )
        observation = cursor.fetchone()
        if (
            observation is None
            or int(observation[0]) < 2
            or str(observation[1]) != expected_sha256
        ):
            raise ManualVersandError(
                'observation stable ou SHA-256 incoherent'
            )

        cursor.execute(
            '''
            SELECT email, empfaengertyp, kontakt_name
            FROM empfaenger
            WHERE auftrag_id = %s AND aktiv = TRUE
            ORDER BY empfaengertyp DESC, email
            FOR UPDATE
            ''',
            (row[6],),
        )
        recipient_rows = tuple(cursor.fetchall())
        if not recipient_rows:
            raise ManualVersandError('aucun Empfaenger actif')
        if not any(str(item[1]) == 'TO' for item in recipient_rows):
            raise ManualVersandError('aucun Empfaenger TO actif')
        snapshot_items: list[dict[str, str]] = []
        recipient_addresses: list[str] = []
        for email, kind, name in recipient_rows:
            address = str(email)
            _assert_example_invalid(address)
            recipient_addresses.append(address)
            snapshot_items.append(
                {
                    'email': address,
                    'kind': str(kind),
                    'name': str(name or ''),
                }
            )
        snapshot = json.dumps(snapshot_items, ensure_ascii=False)
        correlation_id = str(uuid.uuid4())
        dedupe_key = hashlib.sha256(
            (
                'manual-urgent\n'
                f'{row[7]}\n'
                f'{expected_sha256}'
            ).encode('ascii')
        ).hexdigest()
        subject = f'Manueller dringender Versand Auftrag {row[7]}'
        body = (
            f'Operator: {normalized_operator}\n'
            f'Grund: {normalized_reason}\n'
            f'Pruefprotokoll: {row[0]}\n'
        )
        cursor.execute(
            '''
            INSERT INTO versand
                (
                    auftrag_id,
                    correlation_id,
                    dedupe_key,
                    modus,
                    status,
                    mail_provider,
                    empfaenger_snapshot,
                    betreff,
                    body_sha256,
                    planned_at,
                    sending_started_at,
                    versuch_anzahl,
                    manual_actor,
                    manual_reason
                )
            VALUES (
                %s, %s, %s, 'MANUAL', 'MANUALLY_SENT', 'manual',
                %s, %s, %s, %s, %s, 1, %s, %s
            )
            ''',
            (
                row[6],
                correlation_id,
                dedupe_key,
                snapshot,
                subject,
                hashlib.sha256(body.encode('utf-8')).hexdigest(),
                mysql_event_at,
                mysql_event_at,
                normalized_operator,
                normalized_reason,
            ),
        )
        versand_id = int(cursor.lastrowid)
        cursor.execute(
            '''
            INSERT INTO versand_protokoll (versand_id, protokoll_id)
            VALUES (%s, %s)
            ''',
            (versand_id, protokoll_id),
        )
        cursor.execute(
            '''
            UPDATE protokoll
            SET status = 'MANUALLY_SENT', letzte_fehler = NULL
            WHERE id = %s
            ''',
            (protokoll_id,),
        )
        cursor.execute(
            '''
            INSERT INTO audit_event
                (correlation_id, event_type, entity_type, entity_id, actor, details)
            VALUES (
                %s,
                'MANUAL_URGENT_VERSAND_RECORDED',
                'versand',
                %s,
                %s,
                %s
            )
            ''',
            (
                correlation_id,
                versand_id,
                normalized_operator,
                json.dumps(
                    {
                        'protokoll_id': protokoll_id,
                        'filename': str(row[0]),
                        'sha256': expected_sha256,
                        'reason': normalized_reason,
                        'sent_at': event_at.isoformat(),
                        'recipients': recipient_addresses,
                        'eml_created': False,
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        connection.commit()
        return ManualVersandResult(
            versand_id=versand_id,
            protokoll_id=protokoll_id,
            filename=str(row[0]),
            sha256=expected_sha256,
            auftragsnummer=str(row[7]),
            operator=normalized_operator,
            reason=normalized_reason,
            sent_at=event_at,
            recipients=tuple(recipient_addresses),
            protokoll_status='MANUALLY_SENT',
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
