from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from pruefversand.config import Settings
from pruefversand.mock_mail import MockMailProvider
from pruefversand.pdf_tools import calculate_sha256
from pruefversand.versand_review import assert_versand_review_safety


class VersandResolutionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class VersandResolutionDocument:
    protokoll_id: int
    filename: str
    path: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class VersandResolutionResult:
    decision_id: int
    versand_id: int
    decision: str
    reason: str
    decided_at: datetime
    auftragsnummer: str
    versand_status: str
    retry_performed: bool
    message_path: Path | None


@dataclass(frozen=True, slots=True)
class _LockedVersand:
    versand_id: int
    auftragsnummer: str
    attempt_count: int
    recipient_snapshot: str
    subject: str


def _normalized_reason(reason: str) -> str:
    normalized = reason.strip()
    if not normalized:
        raise VersandResolutionError('une raison non vide est obligatoire')
    if len(normalized) > 1000:
        raise VersandResolutionError('la raison depasse 1000 caracteres')
    return normalized


def _lock_incident(cursor: Any, versand_id: int) -> _LockedVersand:
    if versand_id <= 0:
        raise VersandResolutionError('identifiant Versand invalide')
    cursor.execute(
        '''
        SELECT
            v.id,
            v.status,
            v.mail_provider,
            v.versuch_anzahl,
            v.empfaenger_snapshot,
            v.betreff,
            a.auftragsnummer
        FROM versand AS v
        JOIN auftrag AS a ON a.id = v.auftrag_id
        WHERE v.id = %s
        FOR UPDATE
        ''',
        (versand_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise VersandResolutionError('Versand introuvable')
    if str(row[1]) != 'MANUAL_REVIEW':
        raise VersandResolutionError(
            'le Versand n est plus en MANUAL_REVIEW'
        )
    if str(row[2]) != 'mock':
        raise VersandResolutionError(
            'la resolution HOME exige un Versand du fournisseur mock'
        )
    cursor.execute(
        '''
        SELECT decision
        FROM versand_review_decision
        WHERE versand_id = %s
          AND decision IN (
              'PROVIDER_ACCEPTED',
              'PROVIDER_NOT_ACCEPTED_RETRY'
          )
        FOR UPDATE
        ''',
        (versand_id,),
    )
    resolved = cursor.fetchone()
    if resolved is not None:
        raise VersandResolutionError(
            f'une resolution existe deja : {resolved[0]}'
        )
    return _LockedVersand(
        versand_id=int(row[0]),
        auftragsnummer=str(row[6]),
        attempt_count=int(row[3]),
        recipient_snapshot=str(row[4]),
        subject=str(row[5]),
    )


def _load_and_validate_documents(
    cursor: Any,
    settings: Settings,
    versand_id: int,
) -> tuple[VersandResolutionDocument, ...]:
    cursor.execute(
        '''
        SELECT
            p.id,
            p.dateiname,
            p.nas_pfad,
            p.sha256,
            p.dateigroesse,
            p.status,
            p.stable_scan_count
        FROM versand_protokoll AS vp
        JOIN protokoll AS p ON p.id = vp.protokoll_id
        WHERE vp.versand_id = %s
        ORDER BY p.id
        FOR UPDATE
        ''',
        (versand_id,),
    )
    rows = tuple(cursor.fetchall())
    if not rows:
        raise VersandResolutionError(
            'le Versand ne contient aucun Pruefprotokoll lie'
        )
    nas_root = settings.app.nas_path.resolve()
    documents: list[VersandResolutionDocument] = []
    for row in rows:
        if str(row[5]) != 'MANUAL_REVIEW':
            raise VersandResolutionError(
                'un Pruefprotokoll lie n est plus en MANUAL_REVIEW'
            )
        if int(row[6]) < 2:
            raise VersandResolutionError(
                'deux scans stables sont requis avant resolution'
            )
        path = Path(str(row[2])).resolve()
        try:
            path.relative_to(nas_root)
        except ValueError as exc:
            raise VersandResolutionError(
                'un Pruefprotokoll est hors du NAS HOME autorise'
            ) from exc
        if not path.is_file():
            raise VersandResolutionError(
                f'Pruefprotokoll introuvable : {path.name}'
            )
        expected_sha256 = str(row[3])
        if calculate_sha256(path) != expected_sha256:
            raise VersandResolutionError(
                f'SHA-256 modifie depuis le scan : {path.name}'
            )
        if path.stat().st_size != int(row[4]):
            raise VersandResolutionError(
                f'taille modifiee depuis le scan : {path.name}'
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
            raise VersandResolutionError(
                f'observation stable incoherente : {path.name}'
            )
        documents.append(
            VersandResolutionDocument(
                protokoll_id=int(row[0]),
                filename=str(row[1]),
                path=path,
                sha256=expected_sha256,
                size_bytes=int(row[4]),
            )
        )
    return tuple(documents)


def _insert_decision(
    cursor: Any,
    versand_id: int,
    decision: str,
    reason: str,
) -> tuple[int, datetime]:
    cursor.execute(
        '''
        INSERT INTO versand_review_decision
            (versand_id, decision, reason, decided_by)
        VALUES (%s, %s, %s, 'HOME_CLI')
        ''',
        (versand_id, decision, reason),
    )
    decision_id = int(cursor.lastrowid)
    cursor.execute(
        '''
        SELECT decided_at
        FROM versand_review_decision
        WHERE id = %s
        ''',
        (decision_id,),
    )
    return decision_id, cursor.fetchone()[0]


def _record_audit(
    cursor: Any,
    *,
    versand_id: int,
    decision: str,
    reason: str,
    details: dict[str, object] | None = None,
) -> None:
    payload: dict[str, object] = {
        'decision': decision,
        'reason': reason,
        'automatic_retry': False,
    }
    if details:
        payload.update(details)
    cursor.execute(
        '''
        INSERT INTO audit_event
            (event_type, entity_type, entity_id, actor, details)
        VALUES (%s, 'versand', %s, 'HOME_CLI', %s)
        ''',
        (
            f'VERSAND_REVIEW_{decision}',
            versand_id,
            json.dumps(payload, ensure_ascii=False),
        ),
    )


def confirm_provider_accepted(
    connection: Any,
    settings: Settings,
    versand_id: int,
    reason: str,
) -> VersandResolutionResult:
    assert_versand_review_safety(settings)
    normalized_reason = _normalized_reason(reason)
    connection.start_transaction()
    cursor = connection.cursor()
    try:
        incident = _lock_incident(cursor, versand_id)
        if incident.attempt_count < 1:
            raise VersandResolutionError(
                'aucun appel fournisseur n a eu lieu; '
                'confirmation ACCEPTED interdite'
            )
        documents = _load_and_validate_documents(
            cursor,
            settings,
            versand_id,
        )
        decision_id, decided_at = _insert_decision(
            cursor,
            versand_id,
            'PROVIDER_ACCEPTED',
            normalized_reason,
        )
        cursor.execute(
            '''
            UPDATE versand
            SET status = 'ACCEPTED', accepted_at = UTC_TIMESTAMP(6)
            WHERE id = %s
            ''',
            (versand_id,),
        )
        cursor.executemany(
            "UPDATE protokoll SET status = 'ACCEPTED' WHERE id = %s",
            [(document.protokoll_id,) for document in documents],
        )
        _record_audit(
            cursor,
            versand_id=versand_id,
            decision='PROVIDER_ACCEPTED',
            reason=normalized_reason,
            details={'retry_performed': False},
        )
        connection.commit()
        return VersandResolutionResult(
            decision_id=decision_id,
            versand_id=versand_id,
            decision='PROVIDER_ACCEPTED',
            reason=normalized_reason,
            decided_at=decided_at,
            auftragsnummer=incident.auftragsnummer,
            versand_status='ACCEPTED',
            retry_performed=False,
            message_path=None,
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


def keep_provider_result_unknown(
    connection: Any,
    settings: Settings,
    versand_id: int,
    reason: str,
) -> VersandResolutionResult:
    assert_versand_review_safety(settings)
    normalized_reason = _normalized_reason(reason)
    connection.start_transaction()
    cursor = connection.cursor()
    try:
        incident = _lock_incident(cursor, versand_id)
        cursor.execute(
            '''
            SELECT id
            FROM versand_review_decision
            WHERE versand_id = %s AND decision = 'KEEP_UNKNOWN'
            FOR UPDATE
            ''',
            (versand_id,),
        )
        if cursor.fetchone() is not None:
            raise VersandResolutionError(
                'la decision KEEP_UNKNOWN existe deja'
            )
        decision_id, decided_at = _insert_decision(
            cursor,
            versand_id,
            'KEEP_UNKNOWN',
            normalized_reason,
        )
        _record_audit(
            cursor,
            versand_id=versand_id,
            decision='KEEP_UNKNOWN',
            reason=normalized_reason,
            details={
                'status_changed': False,
                'retry_performed': False,
            },
        )
        connection.commit()
        return VersandResolutionResult(
            decision_id=decision_id,
            versand_id=versand_id,
            decision='KEEP_UNKNOWN',
            reason=normalized_reason,
            decided_at=decided_at,
            auftragsnummer=incident.auftragsnummer,
            versand_status='MANUAL_REVIEW',
            retry_performed=False,
            message_path=None,
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


def _recipients_from_snapshot(
    snapshot: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    try:
        raw = json.loads(snapshot)
    except json.JSONDecodeError as exc:
        raise VersandResolutionError(
            'snapshot Empfaenger invalide'
        ) from exc
    if not isinstance(raw, list):
        raise VersandResolutionError('snapshot Empfaenger invalide')
    to: list[str] = []
    cc: list[str] = []
    for item in raw:
        if not isinstance(item, dict):
            raise VersandResolutionError('snapshot Empfaenger invalide')
        email = str(item.get('email', '')).strip()
        kind = str(item.get('kind', '')).upper()
        if kind == 'TO':
            to.append(email)
        elif kind == 'CC':
            cc.append(email)
        else:
            raise VersandResolutionError('type Empfaenger invalide')
    return tuple(to), tuple(cc)


def retry_after_provider_not_accepted(
    connection: Any,
    settings: Settings,
    versand_id: int,
    reason: str,
) -> VersandResolutionResult:
    assert_versand_review_safety(settings)
    normalized_reason = _normalized_reason(reason)
    connection.start_transaction()
    cursor = connection.cursor()
    try:
        incident = _lock_incident(cursor, versand_id)
        if incident.attempt_count == 0:
            raise VersandResolutionError(
                'aucun appel fournisseur initial; retry fournisseur interdit'
            )
        if incident.attempt_count != 1:
            raise VersandResolutionError(
                'la tentative manuelle unique est deja consommee'
            )
        documents = _load_and_validate_documents(
            cursor,
            settings,
            versand_id,
        )
        to, cc = _recipients_from_snapshot(incident.recipient_snapshot)
        decision_id, decided_at = _insert_decision(
            cursor,
            versand_id,
            'PROVIDER_NOT_ACCEPTED_RETRY',
            normalized_reason,
        )
        cursor.execute(
            '''
            UPDATE versand
            SET status = 'SENDING',
                versuch_anzahl = versuch_anzahl + 1,
                sending_started_at = UTC_TIMESTAMP(6)
            WHERE id = %s
            ''',
            (versand_id,),
        )
        cursor.executemany(
            "UPDATE protokoll SET status = 'SENDING' WHERE id = %s",
            [(document.protokoll_id,) for document in documents],
        )
        _record_audit(
            cursor,
            versand_id=versand_id,
            decision='PROVIDER_NOT_ACCEPTED_RETRY',
            reason=normalized_reason,
            details={
                'manual_retry': True,
                'attempt': 2,
                'sha256': [document.sha256 for document in documents],
            },
        )
        connection.commit()
    except Exception:
        connection.rollback()
        cursor.close()
        raise
    cursor.close()

    provider = MockMailProvider(
        settings.mail.mock_outbox_path,
        settings.mail.allowed_recipient_domains,
        settings.app.maximum_attachment_bytes,
        settings.mail.redirect_to,
    )
    try:
        message = provider.send(
            subject=incident.subject,
            body=(
                'HOME-Demonstration: einmaliger manueller Retry. '
                'Keine echte E-Mail wurde versendet.\n'
                f'Auftrag: {incident.auftragsnummer}\n'
                f'Anlagen: {len(documents)}\n'
            ),
            to=to,
            cc=cc,
            attachments=tuple(document.path for document in documents),
        )
    except Exception as exc:
        connection.start_transaction()
        failure_cursor = connection.cursor()
        try:
            failure_reason = f'manueller Retry mock fehlgeschlagen: {exc}'
            failure_cursor.execute(
                '''
                UPDATE versand
                SET status = 'MANUAL_REVIEW', letzte_fehler = %s
                WHERE id = %s
                ''',
                (failure_reason, versand_id),
            )
            failure_cursor.executemany(
                '''
                UPDATE protokoll
                SET status = 'MANUAL_REVIEW', letzte_fehler = %s
                WHERE id = %s
                ''',
                [
                    (failure_reason, document.protokoll_id)
                    for document in documents
                ],
            )
            _record_audit(
                failure_cursor,
                versand_id=versand_id,
                decision='PROVIDER_NOT_ACCEPTED_RETRY',
                reason=normalized_reason,
                details={
                    'manual_retry': True,
                    'retry_failed': True,
                    'error': failure_reason,
                },
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            failure_cursor.close()
        raise VersandResolutionError(failure_reason) from exc

    connection.start_transaction()
    final_cursor = connection.cursor()
    try:
        final_cursor.execute(
            '''
            SELECT status, versuch_anzahl
            FROM versand
            WHERE id = %s
            FOR UPDATE
            ''',
            (versand_id,),
        )
        final_state = final_cursor.fetchone()
        if final_state != ('SENDING', 2):
            raise VersandResolutionError(
                'etat Versand inattendu apres le retry mock'
            )
        final_cursor.execute(
            '''
            UPDATE versand
            SET status = 'ACCEPTED', accepted_at = UTC_TIMESTAMP(6)
            WHERE id = %s
            ''',
            (versand_id,),
        )
        final_cursor.executemany(
            "UPDATE protokoll SET status = 'ACCEPTED' WHERE id = %s",
            [(document.protokoll_id,) for document in documents],
        )
        _record_audit(
            final_cursor,
            versand_id=versand_id,
            decision='PROVIDER_NOT_ACCEPTED_RETRY',
            reason=normalized_reason,
            details={
                'manual_retry': True,
                'retry_accepted_by_mock': True,
                'eml_path': str(message.message_path.resolve()),
                'retry_correlation_id': message.correlation_id,
            },
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        final_cursor.close()
    return VersandResolutionResult(
        decision_id=decision_id,
        versand_id=versand_id,
        decision='PROVIDER_NOT_ACCEPTED_RETRY',
        reason=normalized_reason,
        decided_at=decided_at,
        auftragsnummer=incident.auftragsnummer,
        versand_status='ACCEPTED',
        retry_performed=True,
        message_path=message.message_path,
    )
