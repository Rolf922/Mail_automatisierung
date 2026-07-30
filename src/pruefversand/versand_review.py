from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pruefversand.config import Settings


class VersandReviewSafetyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class VersandReviewDocument:
    protokoll_id: int
    filename: str


@dataclass(frozen=True, slots=True)
class VersandReviewIncident:
    versand_id: int
    auftragsnummer: str
    reason: str
    incident_at: datetime
    linked_documents: tuple[VersandReviewDocument, ...]

    @property
    def linked_document_count(self) -> int:
        return len(self.linked_documents)

    @property
    def automatic_retry_blocked(self) -> bool:
        return bool(self.linked_documents)


def assert_versand_review_safety(settings: Settings) -> None:
    if settings.app.environment not in {'home', 'test'}:
        raise VersandReviewSafetyError(
            'versand-review exige un environnement HOME ou TEST'
        )
    if not settings.app.dry_run:
        raise VersandReviewSafetyError(
            'versand-review exige dry_run = true'
        )
    if settings.mail.provider != 'mock':
        raise VersandReviewSafetyError(
            'versand-review exige mail.provider = mock'
        )
    if settings.mail.allowed_recipient_domains != ('example.invalid',):
        raise VersandReviewSafetyError(
            'versand-review autorise uniquement example.invalid'
        )
    if settings.app.environment == 'home':
        if settings.database.host not in {'127.0.0.1', 'localhost', '::1'}:
            raise VersandReviewSafetyError(
                'versand-review exige MySQL local en HOME'
            )
        if settings.database.port != 3307:
            raise VersandReviewSafetyError(
                'versand-review exige le port MySQL 3307 en HOME'
            )


def list_versand_review_incidents(
    connection: Any,
    settings: Settings,
) -> tuple[VersandReviewIncident, ...]:
    assert_versand_review_safety(settings)
    cursor = connection.cursor()
    try:
        connection.start_transaction(readonly=True)
        cursor.execute(
            '''
            SELECT
                v.id,
                a.auftragsnummer,
                COALESCE(v.letzte_fehler, ''),
                COALESCE(
                    v.sending_started_at,
                    v.updated_at,
                    v.created_at
                ),
                p.id,
                p.dateiname
            FROM versand AS v
            JOIN auftrag AS a ON a.id = v.auftrag_id
            LEFT JOIN versand_protokoll AS vp ON vp.versand_id = v.id
            LEFT JOIN protokoll AS p ON p.id = vp.protokoll_id
            WHERE v.status = 'MANUAL_REVIEW'
            ORDER BY
                COALESCE(
                    v.sending_started_at,
                    v.updated_at,
                    v.created_at
                ),
                v.id,
                p.id
            '''
        )
        grouped: dict[int, dict[str, Any]] = {}
        for row in cursor.fetchall():
            versand_id = int(row[0])
            if versand_id not in grouped:
                grouped[versand_id] = {
                    'auftragsnummer': str(row[1]),
                    'reason': str(row[2]),
                    'incident_at': row[3],
                    'documents': [],
                }
            if row[4] is not None:
                grouped[versand_id]['documents'].append(
                    VersandReviewDocument(
                        protokoll_id=int(row[4]),
                        filename=str(row[5]),
                    )
                )
        incidents = tuple(
            VersandReviewIncident(
                versand_id=versand_id,
                auftragsnummer=str(values['auftragsnummer']),
                reason=str(values['reason']),
                incident_at=values['incident_at'],
                linked_documents=tuple(values['documents']),
            )
            for versand_id, values in grouped.items()
        )
        connection.rollback()
        return incidents
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
