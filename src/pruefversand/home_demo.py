from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
import hashlib
import json
from pathlib import Path
import time
from typing import Any
import uuid

from pruefversand.config import Settings
from pruefversand.database import (
    CsvImportResult,
    apply_migration,
    import_baustellen_csv,
)
from pruefversand.mock_mail import (
    MockMailAmbiguousTimeout,
    MockMailProvider,
    MockSendResult,
)
from pruefversand.models import (
    MailBatch,
    ProtokollStatus,
    ReadyDocument,
    Recipient,
)
from pruefversand.mysql_scanner import scan_and_persist
from pruefversand.pdf_tools import calculate_sha256
from pruefversand.scanner import ScanReport
from pruefversand.schedule import is_weekly_run_due, period_start
from pruefversand.workflow import build_batches


class HomeDemoSafetyError(ValueError):
    pass


class HomeDemoError(RuntimeError):
    pass


AMBIGUOUS_CRASH_AFTER_PROVIDER_REASON = (
    'interruption detectee apres le debut de l appel fournisseur mock; '
    'resultat ambigu, verification humaine requise, aucun retry automatique'
)


class HomeDemoControlledInterruption(HomeDemoError):
    def __init__(self, versand_id: int, correlation_id: str) -> None:
        super().__init__(
            'interruption HOME controlee avant appel du fournisseur mock; '
            f'Versand {versand_id} durable et recuperable'
        )
        self.versand_id = versand_id
        self.correlation_id = correlation_id
        self.provider_called = False


class HomeDemoCrashAfterProvider(HomeDemoError):
    def __init__(
        self,
        versand_id: int,
        correlation_id: str,
        message_path: Path,
    ) -> None:
        super().__init__(
            'crash HOME simule juste apres l appel du fournisseur mock; '
            f'Versand {versand_id} reste SENDING et ne doit pas etre rejoue'
        )
        self.versand_id = versand_id
        self.correlation_id = correlation_id
        self.message_path = message_path
        self.provider_called = True


@dataclass(frozen=True, slots=True)
class HomeDemoResult:
    csv_import: CsvImportResult
    first_scan: ScanReport
    second_scan: ScanReport
    pdf_path: Path
    messages: tuple[MockSendResult, ...]
    reused_message_paths: tuple[Path, ...]
    skipped_auftraege: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ManualReviewDemoResult:
    csv_import: CsvImportResult
    first_scan: ScanReport
    second_scan: ScanReport
    pdf_paths: tuple[Path, ...]
    versand_count_before: int
    versand_count_after: int
    eml_count_before: int
    eml_count_after: int


@dataclass(frozen=True, slots=True)
class MockManualReviewIncident:
    correlation_id: str
    auftragsnummer: str
    reason: str
    versand_id: int | None = None
    incident_type: str = 'AMBIGUOUS_TIMEOUT'
    total_attachment_bytes: int | None = None
    maximum_attachment_bytes: int | None = None
    provider_invoked: bool = True
    message_accepted: bool = False
    eml_created: bool = False
    automatic_retry_blocked: bool = True


MockTimeoutIncident = MockManualReviewIncident


@dataclass(frozen=True, slots=True)
class MockVersandPreparationResult:
    ready_document_count: int
    already_processed_count: int
    weekly_due: bool | None
    period_start: date | None
    messages: tuple[MockSendResult, ...]
    reused_message_paths: tuple[Path, ...]
    skipped_auftraege: tuple[str, ...]
    manual_review_incidents: tuple[MockManualReviewIncident, ...] = ()
    blocked_auftraege: tuple[str, ...] = ()
    resumed_versand_count: int = 0
    provider_call_count: int = 0
    lock_acquired: bool = True
    skipped_due_to_lock: bool = False


@dataclass(frozen=True, slots=True)
class StagedMockVersand:
    versand_id: int
    correlation_id: str
    dedupe_key: str
    batch: MailBatch
    subject: str
    body: str
    period_start: date | None


def _assert_demo_safety(settings: Settings) -> None:
    if settings.app.environment not in {'home', 'test'}:
        raise HomeDemoSafetyError('demo-home exige HOME ou TEST')
    if not settings.app.dry_run:
        raise HomeDemoSafetyError('demo-home exige dry_run = true')
    if settings.mail.provider != 'mock':
        raise HomeDemoSafetyError('demo-home exige mail.provider = mock')
    if settings.mail.allowed_recipient_domains != ('example.invalid',):
        raise HomeDemoSafetyError(
            'demo-home autorise uniquement example.invalid'
        )
    if settings.app.environment == 'home':
        if settings.database.host not in {'127.0.0.1', 'localhost', '::1'}:
            raise HomeDemoSafetyError('demo-home exige MySQL local')
        if settings.database.port != 3307:
            raise HomeDemoSafetyError('demo-home exige le port MySQL 3307')
        expected_nas = (
            settings.project_root / 'sample_data/nas_mock'
        ).resolve()
        if settings.app.nas_path.resolve() != expected_nas:
            raise HomeDemoSafetyError(
                'demo-home exige sample_data/nas_mock'
            )


def _prepare_demo_pdf(nas_path: Path) -> tuple[Path, Path]:
    source = (
        nas_path
        / '00125083_F001_Pruefprotokoll_2026-07-25.pdf'
    )
    if not source.is_file():
        raise FileNotFoundError(
            f'PDF synthetique source introuvable : {source}'
        )
    demo_directory = nas_path / 'demo_home'
    demo_directory.mkdir(parents=True, exist_ok=True)
    target = demo_directory / '00125083_DEMO_Pruefprotokoll.pdf'
    if not target.is_file():
        target.write_bytes(source.read_bytes() + b'\n% DEMO-HOME\n')
    return demo_directory, target


def _prepare_manual_review_pdfs(
    nas_path: Path,
) -> tuple[Path, tuple[Path, ...]]:
    source = (
        nas_path
        / '00125083_F001_Pruefprotokoll_2026-07-25.pdf'
    )
    if not source.is_file():
        raise FileNotFoundError(
            f'PDF synthetique source introuvable : {source}'
        )
    demo_directory = nas_path / 'demo_manual_review'
    demo_directory.mkdir(parents=True, exist_ok=True)
    source_bytes = source.read_bytes()
    unknown = demo_directory / '999999_DEMO_Unbekannt.pdf'
    ambiguous = demo_directory / 'F0661.pdf'
    unknown.write_bytes(
        source_bytes + b'\n% DEMO-MANUAL-UNKNOWN\n'
    )
    ambiguous.write_bytes(
        source_bytes + b'\n% DEMO-MANUAL-AMBIGUOUS\n'
    )
    return demo_directory, (unknown, ambiguous)


def _versand_count(connection: Any) -> int:
    cursor = connection.cursor()
    try:
        cursor.execute('SELECT COUNT(*) FROM versand')
        count = int(cursor.fetchone()[0])
        connection.commit()
        return count
    finally:
        cursor.close()


def run_manual_review_demo(
    connection: Any,
    settings: Settings,
) -> ManualReviewDemoResult:
    _assert_demo_safety(settings)
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

    versand_count_before = _versand_count(connection)
    eml_count_before = len(
        tuple(settings.mail.mock_outbox_path.glob('*.eml'))
    )
    csv_import = import_baustellen_csv(
        connection,
        settings.project_root / 'sample_data/baustellen_example.csv',
    )
    demo_directory, pdf_paths = _prepare_manual_review_pdfs(
        settings.app.nas_path
    )
    first = scan_and_persist(
        connection,
        demo_directory,
        settings.app.filename_pattern,
    )
    second = scan_and_persist(
        connection,
        demo_directory,
        settings.app.filename_pattern,
    )

    if len(first.report.items) != 2 or any(
        item.status != ProtokollStatus.DISCOVERED
        for item in first.report.items
    ):
        raise HomeDemoError(
            'la premiere observation doit rester DISCOVERED'
        )
    if len(second.report.items) != 2 or any(
        item.status != ProtokollStatus.MANUAL_REVIEW
        for item in second.report.items
    ):
        raise HomeDemoError(
            'les PDF inconnu et ambigu doivent passer en MANUAL_REVIEW'
        )

    versand_count_after = _versand_count(connection)
    eml_count_after = len(
        tuple(settings.mail.mock_outbox_path.glob('*.eml'))
    )
    if versand_count_after != versand_count_before:
        raise HomeDemoError(
            'la demonstration MANUAL_REVIEW a cree un Versand'
        )
    if eml_count_after != eml_count_before:
        raise HomeDemoError(
            'la demonstration MANUAL_REVIEW a cree un fichier .eml'
        )

    return ManualReviewDemoResult(
        csv_import=csv_import,
        first_scan=first.report,
        second_scan=second.report,
        pdf_paths=pdf_paths,
        versand_count_before=versand_count_before,
        versand_count_after=versand_count_after,
        eml_count_before=eml_count_before,
        eml_count_after=eml_count_after,
    )


def _load_recipients(
    connection: Any,
    auftragsnummer: str,
) -> tuple[Recipient, ...]:
    cursor = connection.cursor()
    try:
        cursor.execute(
            '''
            SELECT e.email, e.empfaengertyp, e.kontakt_name
            FROM empfaenger AS e
            JOIN auftrag AS a ON a.id = e.auftrag_id
            WHERE a.auftragsnummer = %s
              AND a.aktiv = TRUE
              AND e.aktiv = TRUE
            ORDER BY e.empfaengertyp DESC, e.email
            ''',
            (auftragsnummer,),
        )
        return tuple(
            Recipient(
                email=str(email),
                kind=str(kind),
                name=str(name or ''),
            )
            for email, kind, name in cursor.fetchall()
        )
    finally:
        cursor.close()


def _load_ready_documents(
    connection: Any,
    settings: Settings,
) -> tuple[tuple[ReadyDocument, ...], int]:
    cursor = connection.cursor()
    try:
        cursor.execute(
            '''
            SELECT
                p.nas_pfad,
                a.auftragsnummer,
                p.sha256,
                p.dateigroesse
            FROM protokoll AS p
            JOIN auftrag AS a ON a.id = p.auftrag_id
            WHERE p.status = 'READY'
              AND p.stable_scan_count >= 2
              AND a.aktiv = TRUE
              AND NOT EXISTS (
                  SELECT 1
                  FROM versand_protokoll AS vp
                  WHERE vp.protokoll_id = p.id
              )
            ORDER BY a.auftragsnummer, p.id
            '''
        )
        rows = tuple(cursor.fetchall())
        cursor.execute(
            '''
            SELECT COUNT(*)
            FROM protokoll AS p
            JOIN versand_protokoll AS vp ON vp.protokoll_id = p.id
            '''
        )
        already_processed_count = int(cursor.fetchone()[0])
        connection.commit()
    finally:
        cursor.close()

    nas_root = settings.app.nas_path.resolve()
    documents: list[ReadyDocument] = []
    for path_value, auftragsnummer, sha256, size_bytes in rows:
        path = Path(str(path_value)).resolve()
        try:
            path.relative_to(nas_root)
        except ValueError as exc:
            raise HomeDemoError(
                'Pruefprotokoll READY hors du NAS HOME autorise'
            ) from exc
        if not path.is_file():
            raise HomeDemoError(
                f'Pruefprotokoll READY introuvable : {path}'
            )
        expected_sha256 = str(sha256)
        if calculate_sha256(path) != expected_sha256:
            raise HomeDemoError(
                f'Pruefprotokoll READY modifie apres scan : {path.name}'
            )
        if path.stat().st_size != int(size_bytes):
            raise HomeDemoError(
                f'taille du Pruefprotokoll READY incoherente : {path.name}'
            )
        documents.append(
            ReadyDocument(
                path=path,
                auftragsnummer=str(auftragsnummer),
                sha256=expected_sha256,
                size_bytes=int(size_bytes),
            )
        )
    return tuple(documents), already_processed_count


def _batch_content(batch: MailBatch) -> tuple[str, str]:
    subject = f'Pruefprotokolle Auftrag {batch.auftragsnummer}'
    body = (
        'HOME-Demonstration im dry_run. '
        'Diese Nachricht wurde nicht versendet.\n'
        f'Auftrag: {batch.auftragsnummer}\n'
        f'Anlagen: {len(batch.documents)}\n'
    )
    return subject, body


def _batch_dedupe_key(
    batch: MailBatch,
    weekly_period_start: date | None = None,
) -> str:
    parts = [
        'demo-home',
        weekly_period_start.isoformat() if weekly_period_start else 'immediate',
        batch.auftragsnummer,
        *(document.sha256 for document in batch.documents),
    ]
    return hashlib.sha256('\n'.join(parts).encode('ascii')).hexdigest()


def _find_existing_mock_message(
    outbox_path: Path,
    batch: MailBatch,
) -> tuple[Path, str] | None:
    subject, _ = _batch_content(batch)
    expected_attachments = {
        document.path.name: document.sha256
        for document in batch.documents
    }
    for path in sorted(
        outbox_path.glob('*.eml'),
        key=lambda item: item.stat().st_mtime_ns,
        reverse=True,
    ):
        message = BytesParser(policy=policy.default).parsebytes(
            path.read_bytes()
        )
        if str(message.get('Subject', '')) != f'[TEST] {subject}':
            continue
        recipients = [
            address
            for _, address in getaddresses(
                [
                    str(message.get('To', '')),
                    str(message.get('Cc', '')),
                ]
            )
            if address
        ]
        if not recipients or any(
            not address.endswith('@example.invalid')
            for address in recipients
        ):
            continue
        actual_attachments = {
            str(part.get_filename()): hashlib.sha256(
                part.get_payload(decode=True)
            ).hexdigest()
            for part in message.iter_attachments()
            if part.get_filename()
        }
        if actual_attachments != expected_attachments:
            continue
        correlation_id = str(
            message.get('X-Pruefversand-Correlation-ID', '')
        )
        if correlation_id:
            return path, correlation_id
    return None


def _find_mock_message_by_correlation(
    outbox_path: Path,
    correlation_id: str,
) -> Path | None:
    for path in sorted(outbox_path.glob('*.eml')):
        message = BytesParser(policy=policy.default).parsebytes(
            path.read_bytes()
        )
        if (
            str(message.get('X-Pruefversand-Correlation-ID', ''))
            == correlation_id
        ):
            return path
    return None


def _versand_exists(connection: Any, dedupe_key: str) -> bool:
    cursor = connection.cursor()
    try:
        cursor.execute(
            'SELECT id FROM versand WHERE dedupe_key = %s',
            (dedupe_key,),
        )
        exists = cursor.fetchone() is not None
        connection.commit()
        return exists
    finally:
        cursor.close()


def _mysql_event_at(run_at: datetime | None) -> datetime:
    event_at = run_at or datetime.now(timezone.utc)
    if event_at.tzinfo is None:
        raise HomeDemoError('date Versand sans fuseau horaire')
    return event_at.astimezone(timezone.utc).replace(tzinfo=None)


def _recipient_snapshot(batch: MailBatch) -> str:
    return json.dumps(
        [
            {
                'email': recipient.email,
                'kind': recipient.kind,
                'name': recipient.name,
            }
            for recipient in batch.recipients
        ],
        ensure_ascii=False,
    )


def _stage_mock_versand(
    connection: Any,
    batch: MailBatch,
    *,
    dedupe_key: str,
    subject: str,
    body: str,
    weekly_period_start: date | None = None,
    run_at: datetime | None = None,
) -> StagedMockVersand | None:
    correlation_id = str(uuid.uuid4())
    mysql_event_at = _mysql_event_at(run_at)
    connection.start_transaction()
    cursor = connection.cursor()
    try:
        cursor.execute(
            'SELECT id FROM versand WHERE dedupe_key = %s FOR UPDATE',
            (dedupe_key,),
        )
        if cursor.fetchone() is not None:
            connection.commit()
            return None

        cursor.execute(
            '''
            SELECT id
            FROM auftrag
            WHERE auftragsnummer = %s AND aktiv = TRUE
            FOR UPDATE
            ''',
            (batch.auftragsnummer,),
        )
        auftrag_row = cursor.fetchone()
        if auftrag_row is None:
            raise HomeDemoError(
                'Auftrag actif absent pendant la preparation durable'
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
                    period_start,
                    planned_at,
                    versuch_anzahl
                )
            VALUES (
                %s, %s, %s, 'AUTOMATIC', 'PLANNED', 'mock',
                %s, %s, %s, %s, %s, 0
            )
            ''',
            (
                auftrag_row[0],
                correlation_id,
                dedupe_key,
                _recipient_snapshot(batch),
                subject,
                hashlib.sha256(body.encode('utf-8')).hexdigest(),
                weekly_period_start,
                mysql_event_at,
            ),
        )
        versand_id = int(cursor.lastrowid)
        attachments: list[dict[str, object]] = []
        for document in batch.documents:
            cursor.execute(
                '''
                SELECT id, status, stable_scan_count, dateigroesse
                FROM protokoll
                WHERE sha256 = %s
                FOR UPDATE
                ''',
                (document.sha256,),
            )
            protokoll_row = cursor.fetchone()
            if protokoll_row is None:
                raise HomeDemoError(
                    'Pruefprotokoll absent pendant la preparation durable'
                )
            if (
                str(protokoll_row[1]) != 'READY'
                or int(protokoll_row[2]) < 2
                or int(protokoll_row[3]) != document.size_bytes
            ):
                raise HomeDemoError(
                    'Pruefprotokoll non eligible pendant la preparation durable'
                )
            cursor.execute(
                '''
                INSERT INTO versand_protokoll (versand_id, protokoll_id)
                VALUES (%s, %s)
                ''',
                (versand_id, protokoll_row[0]),
            )
            cursor.execute(
                '''
                UPDATE protokoll
                SET status = 'SENDING', letzte_fehler = NULL
                WHERE id = %s
                ''',
                (protokoll_row[0],),
            )
            attachments.append(
                {
                    'name': document.path.name,
                    'sha256': document.sha256,
                }
            )
        cursor.execute(
            '''
            INSERT INTO audit_event
                (correlation_id, event_type, entity_type, entity_id, details)
            VALUES (
                %s,
                'MOCK_VERSAND_STAGED',
                'versand',
                %s,
                %s
            )
            ''',
            (
                correlation_id,
                versand_id,
                json.dumps(
                    {
                        'provider_called': False,
                        'recoverable_before_provider': True,
                        'attachments': attachments,
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        connection.commit()
        return StagedMockVersand(
            versand_id=versand_id,
            correlation_id=correlation_id,
            dedupe_key=dedupe_key,
            batch=batch,
            subject=subject,
            body=body,
            period_start=weekly_period_start,
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


def _load_staged_mock_versand(
    connection: Any,
    settings: Settings,
) -> tuple[StagedMockVersand, ...]:
    cursor = connection.cursor()
    try:
        cursor.execute(
            '''
            SELECT
                v.id,
                v.correlation_id,
                v.dedupe_key,
                a.auftragsnummer,
                v.empfaenger_snapshot,
                v.betreff,
                v.body_sha256,
                v.period_start,
                p.nas_pfad,
                p.sha256,
                p.dateigroesse,
                p.stable_scan_count,
                p.status
            FROM versand AS v
            JOIN auftrag AS a ON a.id = v.auftrag_id
            JOIN versand_protokoll AS vp ON vp.versand_id = v.id
            JOIN protokoll AS p ON p.id = vp.protokoll_id
            WHERE v.modus = 'AUTOMATIC'
              AND v.mail_provider = 'mock'
              AND v.status = 'PLANNED'
              AND v.versuch_anzahl = 0
              AND a.aktiv = TRUE
            ORDER BY v.id, p.id
            '''
        )
        rows = tuple(cursor.fetchall())
        connection.commit()
    finally:
        cursor.close()

    grouped: dict[int, dict[str, object]] = {}
    nas_root = settings.app.nas_path.resolve()
    for row in rows:
        versand_id = int(row[0])
        path = Path(str(row[8])).resolve()
        try:
            path.relative_to(nas_root)
        except ValueError as exc:
            raise HomeDemoError(
                'Pruefprotokoll PLANNED hors du NAS HOME autorise'
            ) from exc
        if str(row[12]) != 'SENDING' or int(row[11]) < 2:
            raise HomeDemoError(
                'etat du Pruefprotokoll PLANNED non recuperable'
            )
        if not path.is_file():
            raise HomeDemoError(
                f'Pruefprotokoll PLANNED introuvable : {path}'
            )
        expected_sha256 = str(row[9])
        if calculate_sha256(path) != expected_sha256:
            raise HomeDemoError(
                f'Pruefprotokoll PLANNED modifie : {path.name}'
            )
        if path.stat().st_size != int(row[10]):
            raise HomeDemoError(
                f'taille du Pruefprotokoll PLANNED incoherente : {path.name}'
            )
        group = grouped.setdefault(
            versand_id,
            {
                'correlation_id': str(row[1]),
                'dedupe_key': str(row[2]),
                'auftragsnummer': str(row[3]),
                'snapshot': row[4],
                'subject': str(row[5]),
                'body_sha256': str(row[6]),
                'period_start': row[7],
                'documents': [],
            },
        )
        documents = group['documents']
        assert isinstance(documents, list)
        documents.append(
            ReadyDocument(
                path=path,
                auftragsnummer=str(row[3]),
                sha256=expected_sha256,
                size_bytes=int(row[10]),
            )
        )

    staged: list[StagedMockVersand] = []
    for versand_id, group in grouped.items():
        snapshot = group['snapshot']
        if isinstance(snapshot, (bytes, bytearray)):
            snapshot = snapshot.decode('utf-8')
        if isinstance(snapshot, str):
            snapshot = json.loads(snapshot)
        if not isinstance(snapshot, list):
            raise HomeDemoError('instantane Empfaenger PLANNED invalide')
        recipients = tuple(
            Recipient(
                email=str(item['email']),
                kind=str(item['kind']),
                name=str(item.get('name', '')),
            )
            for item in snapshot
        )
        if not recipients or any(
            not recipient.email.lower().endswith('@example.invalid')
            for recipient in recipients
        ):
            raise HomeDemoError(
                'Empfaenger PLANNED hors example.invalid'
            )
        documents = tuple(group['documents'])
        batch = MailBatch(
            auftragsnummer=str(group['auftragsnummer']),
            documents=documents,
            recipients=recipients,
        )
        expected_subject, body = _batch_content(batch)
        if str(group['subject']) != expected_subject:
            raise HomeDemoError('objet PLANNED incoherent')
        if (
            hashlib.sha256(body.encode('utf-8')).hexdigest()
            != str(group['body_sha256'])
        ):
            raise HomeDemoError('corps PLANNED incoherent')
        staged.append(
            StagedMockVersand(
                versand_id=versand_id,
                correlation_id=str(group['correlation_id']),
                dedupe_key=str(group['dedupe_key']),
                batch=batch,
                subject=expected_subject,
                body=body,
                period_start=group['period_start'],
            )
        )
    return tuple(staged)


def _start_staged_provider_call(
    connection: Any,
    staged: StagedMockVersand,
    run_at: datetime | None,
) -> None:
    mysql_event_at = _mysql_event_at(run_at)
    connection.start_transaction()
    cursor = connection.cursor()
    try:
        cursor.execute(
            '''
            SELECT status, versuch_anzahl
            FROM versand
            WHERE id = %s
            FOR UPDATE
            ''',
            (staged.versand_id,),
        )
        row = cursor.fetchone()
        if row is None or (str(row[0]), int(row[1])) != ('PLANNED', 0):
            raise HomeDemoError(
                'Versand PLANNED non recuperable avant fournisseur'
            )
        cursor.execute(
            '''
            UPDATE versand
            SET status = 'SENDING', sending_started_at = %s,
                versuch_anzahl = 1
            WHERE id = %s
            ''',
            (mysql_event_at, staged.versand_id),
        )
        cursor.execute(
            '''
            INSERT INTO audit_event
                (correlation_id, event_type, entity_type, entity_id, details)
            VALUES (
                %s,
                'MOCK_PROVIDER_CALL_STARTED',
                'versand',
                %s,
                %s
            )
            ''',
            (
                staged.correlation_id,
                staged.versand_id,
                json.dumps(
                    {
                        'attempt': 1,
                        'network_connection': False,
                    }
                ),
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


def _accept_staged_mock_versand(
    connection: Any,
    staged: StagedMockVersand,
    message: MockSendResult,
    run_at: datetime | None,
    *,
    resumed: bool,
) -> None:
    mysql_event_at = _mysql_event_at(run_at)
    connection.start_transaction()
    cursor = connection.cursor()
    try:
        cursor.execute(
            'SELECT status, versuch_anzahl FROM versand WHERE id = %s FOR UPDATE',
            (staged.versand_id,),
        )
        row = cursor.fetchone()
        if row is None or (str(row[0]), int(row[1])) != ('SENDING', 1):
            raise HomeDemoError('Versand SENDING incoherent apres mock')
        cursor.execute(
            '''
            UPDATE versand
            SET status = 'ACCEPTED', accepted_at = %s, letzte_fehler = NULL
            WHERE id = %s
            ''',
            (mysql_event_at, staged.versand_id),
        )
        cursor.execute(
            '''
            UPDATE protokoll AS p
            JOIN versand_protokoll AS vp ON vp.protokoll_id = p.id
            SET p.status = 'ACCEPTED', p.letzte_fehler = NULL
            WHERE vp.versand_id = %s
            ''',
            (staged.versand_id,),
        )
        cursor.execute(
            '''
            INSERT INTO audit_event
                (correlation_id, event_type, entity_type, entity_id, details)
            VALUES (%s, 'MOCK_VERSAND_CREATED', 'versand', %s, %s)
            ''',
            (
                staged.correlation_id,
                staged.versand_id,
                json.dumps(
                    {
                        'eml_path': str(message.message_path.resolve()),
                        'reconciled': False,
                        'resumed_from_planned': resumed,
                    }
                ),
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


def _mark_staged_timeout_manual_review(
    connection: Any,
    staged: StagedMockVersand,
    timeout: MockMailAmbiguousTimeout,
) -> MockTimeoutIncident:
    connection.start_transaction()
    cursor = connection.cursor()
    try:
        cursor.execute(
            'SELECT status, versuch_anzahl FROM versand WHERE id = %s FOR UPDATE',
            (staged.versand_id,),
        )
        row = cursor.fetchone()
        if row is None or (str(row[0]), int(row[1])) != ('SENDING', 1):
            raise HomeDemoError('Versand SENDING incoherent apres timeout')
        cursor.execute(
            '''
            UPDATE versand
            SET status = 'MANUAL_REVIEW', letzte_fehler = %s
            WHERE id = %s
            ''',
            (timeout.reason, staged.versand_id),
        )
        cursor.execute(
            '''
            UPDATE protokoll AS p
            JOIN versand_protokoll AS vp ON vp.protokoll_id = p.id
            SET p.status = 'MANUAL_REVIEW', p.letzte_fehler = %s
            WHERE vp.versand_id = %s
            ''',
            (timeout.reason, staged.versand_id),
        )
        cursor.execute(
            '''
            INSERT INTO audit_event
                (correlation_id, event_type, entity_type, entity_id, details)
            VALUES (
                %s,
                'MOCK_MAIL_TIMEOUT_AMBIGUOUS',
                'versand',
                %s,
                %s
            )
            ''',
            (
                staged.correlation_id,
                staged.versand_id,
                json.dumps(
                    {
                        'reason': timeout.reason,
                        'eml_created': False,
                        'automatic_retry': False,
                        'network_connection': False,
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        connection.commit()
        return MockTimeoutIncident(
            correlation_id=staged.correlation_id,
            auftragsnummer=staged.batch.auftragsnummer,
            reason=timeout.reason,
            versand_id=staged.versand_id,
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


def _recover_ambiguous_sending_mock_versand(
    connection: Any,
    settings: Settings,
) -> tuple[MockManualReviewIncident, ...]:
    cursor = connection.cursor()
    try:
        cursor.execute(
            '''
            SELECT v.id, v.correlation_id, a.auftragsnummer
            FROM versand AS v
            JOIN auftrag AS a ON a.id = v.auftrag_id
            WHERE v.modus = 'AUTOMATIC'
              AND v.mail_provider = 'mock'
              AND v.status = 'SENDING'
              AND v.versuch_anzahl > 0
              AND v.accepted_at IS NULL
            ORDER BY v.id
            FOR UPDATE
            '''
        )
        rows = tuple(cursor.fetchall())
        incidents: list[MockManualReviewIncident] = []
        for versand_id_value, correlation_value, auftragsnummer_value in rows:
            versand_id = int(versand_id_value)
            correlation_id = str(correlation_value)
            auftragsnummer = str(auftragsnummer_value)
            message_path = _find_mock_message_by_correlation(
                settings.mail.mock_outbox_path,
                correlation_id,
            )
            cursor.execute(
                '''
                UPDATE versand
                SET status = 'MANUAL_REVIEW', letzte_fehler = %s
                WHERE id = %s
                  AND status = 'SENDING'
                ''',
                (AMBIGUOUS_CRASH_AFTER_PROVIDER_REASON, versand_id),
            )
            cursor.execute(
                '''
                UPDATE protokoll AS p
                JOIN versand_protokoll AS vp ON vp.protokoll_id = p.id
                SET p.status = 'MANUAL_REVIEW', p.letzte_fehler = %s
                WHERE vp.versand_id = %s
                  AND p.status = 'SENDING'
                ''',
                (AMBIGUOUS_CRASH_AFTER_PROVIDER_REASON, versand_id),
            )
            details: dict[str, object] = {
                'reason': AMBIGUOUS_CRASH_AFTER_PROVIDER_REASON,
                'provider_invoked': True,
                'message_accepted': False,
                'eml_created': message_path is not None,
                'automatic_retry': False,
                'network_connection': False,
            }
            if message_path is not None:
                details['eml_path'] = str(message_path.resolve())
            cursor.execute(
                '''
                INSERT INTO audit_event
                    (
                        correlation_id,
                        event_type,
                        entity_type,
                        entity_id,
                        details
                    )
                VALUES (
                    %s,
                    'MOCK_PROVIDER_RESULT_AMBIGUOUS_AFTER_CRASH',
                    'versand',
                    %s,
                    %s
                )
                ''',
                (
                    correlation_id,
                    versand_id,
                    json.dumps(details, ensure_ascii=False),
                ),
            )
            incidents.append(
                MockManualReviewIncident(
                    correlation_id=correlation_id,
                    auftragsnummer=auftragsnummer,
                    reason=AMBIGUOUS_CRASH_AFTER_PROVIDER_REASON,
                    versand_id=versand_id,
                    incident_type='CRASH_AFTER_PROVIDER',
                    provider_invoked=True,
                    message_accepted=False,
                    eml_created=message_path is not None,
                    automatic_retry_blocked=True,
                )
            )
        connection.commit()
        return tuple(incidents)
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


def _send_staged_mock_versand(
    connection: Any,
    settings: Settings,
    staged: StagedMockVersand,
    *,
    run_at: datetime | None = None,
    simulate_timeout: bool = False,
    simulate_crash_after_provider: bool = False,
    resumed: bool = False,
) -> tuple[MockSendResult | None, MockTimeoutIncident | None]:
    provider = MockMailProvider(
        settings.mail.mock_outbox_path,
        settings.mail.allowed_recipient_domains,
        settings.app.maximum_attachment_bytes,
        settings.mail.redirect_to,
        simulate_timeout=simulate_timeout,
    )
    to = tuple(
        recipient.email
        for recipient in staged.batch.recipients
        if recipient.kind == 'TO'
    )
    cc = tuple(
        recipient.email
        for recipient in staged.batch.recipients
        if recipient.kind == 'CC'
    )
    _start_staged_provider_call(connection, staged, run_at)
    try:
        message = provider.send(
            subject=staged.subject,
            body=staged.body,
            to=to,
            cc=cc,
            attachments=tuple(
                document.path for document in staged.batch.documents
            ),
            correlation_id=staged.correlation_id,
        )
    except MockMailAmbiguousTimeout as timeout:
        return None, _mark_staged_timeout_manual_review(
            connection,
            staged,
            timeout,
        )
    if simulate_crash_after_provider:
        raise HomeDemoCrashAfterProvider(
            staged.versand_id,
            staged.correlation_id,
            message.message_path,
        )
    _accept_staged_mock_versand(
        connection,
        staged,
        message,
        run_at,
        resumed=resumed,
    )
    return message, None


def _record_mock_versand(
    connection: Any,
    batch: MailBatch,
    *,
    correlation_id: str,
    dedupe_key: str,
    subject: str,
    body: str,
    message_path: Path,
    reconciled: bool,
    weekly_period_start: date | None = None,
    run_at: datetime | None = None,
) -> bool:
    connection.start_transaction()
    cursor = connection.cursor()
    try:
        event_at = run_at or datetime.now(timezone.utc)
        if event_at.tzinfo is None:
            raise HomeDemoError('date Versand sans fuseau horaire')
        mysql_event_at = event_at.astimezone(timezone.utc).replace(
            tzinfo=None
        )
        cursor.execute(
            'SELECT id FROM versand WHERE dedupe_key = %s FOR UPDATE',
            (dedupe_key,),
        )
        if cursor.fetchone() is not None:
            connection.commit()
            return False

        cursor.execute(
            'SELECT id FROM auftrag WHERE auftragsnummer = %s',
            (batch.auftragsnummer,),
        )
        auftrag_row = cursor.fetchone()
        if auftrag_row is None:
            raise HomeDemoError('Auftrag absent pendant le Versand mock')

        snapshot = json.dumps(
            [
                {
                    'email': recipient.email,
                    'kind': recipient.kind,
                    'name': recipient.name,
                }
                for recipient in batch.recipients
            ],
            ensure_ascii=False,
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
                    period_start,
                    planned_at,
                    sending_started_at,
                    accepted_at,
                    versuch_anzahl
                )
            VALUES (
                %s, %s, %s, 'AUTOMATIC', 'ACCEPTED', 'mock',
                %s, %s, %s, %s,
                %s, %s, %s, 1
            )
            ''',
            (
                auftrag_row[0],
                correlation_id,
                dedupe_key,
                snapshot,
                subject,
                hashlib.sha256(body.encode('utf-8')).hexdigest(),
                weekly_period_start,
                mysql_event_at,
                mysql_event_at,
                mysql_event_at,
            ),
        )
        versand_id = cursor.lastrowid
        for document in batch.documents:
            cursor.execute(
                'SELECT id FROM protokoll WHERE sha256 = %s FOR UPDATE',
                (document.sha256,),
            )
            protokoll_row = cursor.fetchone()
            if protokoll_row is None:
                raise HomeDemoError(
                    'Pruefprotokoll absent pendant le Versand mock'
                )
            cursor.execute(
                '''
                INSERT INTO versand_protokoll (versand_id, protokoll_id)
                VALUES (%s, %s)
                ''',
                (versand_id, protokoll_row[0]),
            )
            cursor.execute(
                '''
                UPDATE protokoll
                SET status = 'ACCEPTED'
                WHERE id = %s
                ''',
                (protokoll_row[0],),
            )
        cursor.execute(
            '''
            INSERT INTO audit_event
                (correlation_id, event_type, entity_type, entity_id, details)
            VALUES (%s, %s, 'versand', %s, %s)
            ''',
            (
                correlation_id,
                (
                    'MOCK_VERSAND_RECONCILED'
                    if reconciled
                    else 'MOCK_VERSAND_CREATED'
                ),
                versand_id,
                json.dumps(
                    {
                        'eml_path': str(message_path.resolve()),
                        'reconciled': reconciled,
                    }
                ),
            ),
        )
        connection.commit()
        return True
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


def _record_mock_timeout_manual_review(
    connection: Any,
    batch: MailBatch,
    *,
    timeout: MockMailAmbiguousTimeout,
    dedupe_key: str,
    subject: str,
    body: str,
    weekly_period_start: date | None = None,
    run_at: datetime | None = None,
) -> MockTimeoutIncident | None:
    connection.start_transaction()
    cursor = connection.cursor()
    try:
        event_at = run_at or datetime.now(timezone.utc)
        if event_at.tzinfo is None:
            raise HomeDemoError('date Versand sans fuseau horaire')
        mysql_event_at = event_at.astimezone(timezone.utc).replace(
            tzinfo=None
        )
        cursor.execute(
            'SELECT id FROM versand WHERE dedupe_key = %s FOR UPDATE',
            (dedupe_key,),
        )
        if cursor.fetchone() is not None:
            connection.commit()
            return None

        cursor.execute(
            '''
            SELECT id
            FROM auftrag
            WHERE auftragsnummer = %s AND aktiv = TRUE
            FOR UPDATE
            ''',
            (batch.auftragsnummer,),
        )
        auftrag_row = cursor.fetchone()
        if auftrag_row is None:
            raise HomeDemoError(
                'Auftrag actif absent pendant le timeout mock'
            )

        snapshot = json.dumps(
            [
                {
                    'email': recipient.email,
                    'kind': recipient.kind,
                    'name': recipient.name,
                }
                for recipient in batch.recipients
            ],
            ensure_ascii=False,
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
                    period_start,
                    planned_at,
                    sending_started_at,
                    versuch_anzahl,
                    letzte_fehler
                )
            VALUES (
                %s, %s, %s, 'AUTOMATIC', 'MANUAL_REVIEW', 'mock',
                %s, %s, %s, %s, %s, %s, 1, %s
            )
            ''',
            (
                auftrag_row[0],
                timeout.correlation_id,
                dedupe_key,
                snapshot,
                subject,
                hashlib.sha256(body.encode('utf-8')).hexdigest(),
                weekly_period_start,
                mysql_event_at,
                mysql_event_at,
                timeout.reason,
            ),
        )
        versand_id = cursor.lastrowid
        for document in batch.documents:
            cursor.execute(
                'SELECT id FROM protokoll WHERE sha256 = %s FOR UPDATE',
                (document.sha256,),
            )
            protokoll_row = cursor.fetchone()
            if protokoll_row is None:
                raise HomeDemoError(
                    'Pruefprotokoll absent pendant le timeout mock'
                )
            cursor.execute(
                '''
                INSERT INTO versand_protokoll (versand_id, protokoll_id)
                VALUES (%s, %s)
                ''',
                (versand_id, protokoll_row[0]),
            )
            cursor.execute(
                '''
                UPDATE protokoll
                SET status = 'MANUAL_REVIEW', letzte_fehler = %s
                WHERE id = %s
                ''',
                (timeout.reason, protokoll_row[0]),
            )
        cursor.execute(
            '''
            INSERT INTO audit_event
                (correlation_id, event_type, entity_type, entity_id, details)
            VALUES (
                %s,
                'MOCK_MAIL_TIMEOUT_AMBIGUOUS',
                'versand',
                %s,
                %s
            )
            ''',
            (
                timeout.correlation_id,
                versand_id,
                json.dumps(
                    {
                        'reason': timeout.reason,
                        'eml_created': False,
                        'automatic_retry': False,
                        'network_connection': False,
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        connection.commit()
        return MockTimeoutIncident(
            correlation_id=timeout.correlation_id,
            auftragsnummer=batch.auftragsnummer,
            reason=timeout.reason,
            versand_id=int(versand_id),
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


def _attachment_limit_reason(total_bytes: int, limit_bytes: int) -> str:
    return (
        'limite cumulee des pieces jointes depassee: '
        f'total={total_bytes} octets; limite={limit_bytes} octets; '
        'lot complet bloque sans appel fournisseur, sans decoupage et '
        'sans retry automatique'
    )


def _record_attachment_limit_manual_review(
    connection: Any,
    batch: MailBatch,
    *,
    dedupe_key: str,
    subject: str,
    body: str,
    total_bytes: int,
    limit_bytes: int,
    weekly_period_start: date | None = None,
    run_at: datetime | None = None,
) -> MockManualReviewIncident | None:
    correlation_id = str(uuid.uuid4())
    reason = _attachment_limit_reason(total_bytes, limit_bytes)
    mysql_event_at = _mysql_event_at(run_at)
    connection.start_transaction()
    cursor = connection.cursor()
    try:
        cursor.execute(
            'SELECT id FROM versand WHERE dedupe_key = %s FOR UPDATE',
            (dedupe_key,),
        )
        if cursor.fetchone() is not None:
            connection.commit()
            return None

        cursor.execute(
            '''
            SELECT id
            FROM auftrag
            WHERE auftragsnummer = %s AND aktiv = TRUE
            FOR UPDATE
            ''',
            (batch.auftragsnummer,),
        )
        auftrag_row = cursor.fetchone()
        if auftrag_row is None:
            raise HomeDemoError(
                'Auftrag actif absent pendant le blocage de taille'
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
                    period_start,
                    planned_at,
                    versuch_anzahl,
                    letzte_fehler
                )
            VALUES (
                %s, %s, %s, 'AUTOMATIC', 'MANUAL_REVIEW', 'mock',
                %s, %s, %s, %s, %s, 0, %s
            )
            ''',
            (
                auftrag_row[0],
                correlation_id,
                dedupe_key,
                _recipient_snapshot(batch),
                subject,
                hashlib.sha256(body.encode('utf-8')).hexdigest(),
                weekly_period_start,
                mysql_event_at,
                reason,
            ),
        )
        versand_id = int(cursor.lastrowid)
        attachment_details: list[dict[str, object]] = []
        for document in batch.documents:
            cursor.execute(
                '''
                SELECT id, status, stable_scan_count, dateigroesse
                FROM protokoll
                WHERE sha256 = %s
                FOR UPDATE
                ''',
                (document.sha256,),
            )
            protokoll_row = cursor.fetchone()
            if protokoll_row is None:
                raise HomeDemoError(
                    'Pruefprotokoll absent pendant le blocage de taille'
                )
            if (
                str(protokoll_row[1]) != 'READY'
                or int(protokoll_row[2]) < 2
                or int(protokoll_row[3]) != document.size_bytes
            ):
                raise HomeDemoError(
                    'Pruefprotokoll non eligible pendant le blocage de taille'
                )
            cursor.execute(
                '''
                INSERT INTO versand_protokoll (versand_id, protokoll_id)
                VALUES (%s, %s)
                ''',
                (versand_id, protokoll_row[0]),
            )
            cursor.execute(
                '''
                UPDATE protokoll
                SET status = 'MANUAL_REVIEW', letzte_fehler = %s
                WHERE id = %s
                ''',
                (reason, protokoll_row[0]),
            )
            attachment_details.append(
                {
                    'name': document.path.name,
                    'sha256': document.sha256,
                    'size_bytes': document.size_bytes,
                }
            )

        cursor.execute(
            '''
            INSERT INTO audit_event
                (correlation_id, event_type, entity_type, entity_id, details)
            VALUES (
                %s,
                'ATTACHMENT_LIMIT_EXCEEDED',
                'versand',
                %s,
                %s
            )
            ''',
            (
                correlation_id,
                versand_id,
                json.dumps(
                    {
                        'reason': reason,
                        'total_attachment_bytes': total_bytes,
                        'maximum_attachment_bytes': limit_bytes,
                        'attachment_count': len(batch.documents),
                        'attachments': attachment_details,
                        'provider_invoked': False,
                        'message_accepted': False,
                        'eml_created': False,
                        'split_performed': False,
                        'automatic_retry': False,
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        connection.commit()
        return MockManualReviewIncident(
            correlation_id=correlation_id,
            auftragsnummer=batch.auftragsnummer,
            reason=reason,
            versand_id=versand_id,
            incident_type='ATTACHMENT_LIMIT_EXCEEDED',
            total_attachment_bytes=total_bytes,
            maximum_attachment_bytes=limit_bytes,
            provider_invoked=False,
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


def _prepare_mock_versand_documents(
    connection: Any,
    settings: Settings,
    ready_documents: tuple[ReadyDocument, ...],
    *,
    already_processed_count: int = 0,
    weekly_due: bool | None = None,
    weekly_period_start: date | None = None,
    run_at: datetime | None = None,
    simulate_timeout: bool = False,
    simulate_interruption_before_provider: bool = False,
    simulate_crash_after_provider: bool = False,
) -> MockVersandPreparationResult:
    if not ready_documents:
        return MockVersandPreparationResult(
            ready_document_count=0,
            already_processed_count=already_processed_count,
            weekly_due=weekly_due,
            period_start=weekly_period_start,
            messages=(),
            reused_message_paths=(),
            skipped_auftraege=(),
        )

    recipients_by_auftrag = {
        auftragsnummer: _load_recipients(connection, auftragsnummer)
        for auftragsnummer in {
            document.auftragsnummer for document in ready_documents
        }
    }
    batches = build_batches(ready_documents, recipients_by_auftrag)
    if batches.blocked:
        blocked = ', '.join(
            item.auftragsnummer for item in batches.blocked
        )
        raise HomeDemoError(
            f'Pruefprotokoll READY sans Empfaenger actif : {blocked}'
        )

    messages: list[MockSendResult] = []
    reused_message_paths: list[Path] = []
    skipped_auftraege: list[str] = []
    manual_review_incidents: list[MockManualReviewIncident] = []
    blocked_auftraege: list[str] = []
    provider_call_count = 0
    for batch in batches.batches:
        subject, body = _batch_content(batch)
        dedupe_key = _batch_dedupe_key(batch, weekly_period_start)
        existing_message = _find_existing_mock_message(
            settings.mail.mock_outbox_path,
            batch,
        )
        if _versand_exists(connection, dedupe_key):
            skipped_auftraege.append(batch.auftragsnummer)
            if existing_message is not None:
                reused_message_paths.append(existing_message[0])
            continue
        total_attachment_bytes = sum(
            document.size_bytes for document in batch.documents
        )
        if total_attachment_bytes > settings.app.maximum_attachment_bytes:
            incident = _record_attachment_limit_manual_review(
                connection,
                batch,
                dedupe_key=dedupe_key,
                subject=subject,
                body=body,
                total_bytes=total_attachment_bytes,
                limit_bytes=settings.app.maximum_attachment_bytes,
                weekly_period_start=weekly_period_start,
                run_at=run_at,
            )
            if incident is None:
                skipped_auftraege.append(batch.auftragsnummer)
            else:
                manual_review_incidents.append(incident)
                blocked_auftraege.append(batch.auftragsnummer)
            continue
        if existing_message is not None:
            _record_mock_versand(
                connection,
                batch,
                correlation_id=existing_message[1],
                dedupe_key=dedupe_key,
                subject=subject,
                body=body,
                message_path=existing_message[0],
                reconciled=True,
                weekly_period_start=weekly_period_start,
                run_at=run_at,
            )
            reused_message_paths.append(existing_message[0])
            skipped_auftraege.append(batch.auftragsnummer)
            continue

        staged = _stage_mock_versand(
            connection,
            batch,
            dedupe_key=dedupe_key,
            subject=subject,
            body=body,
            weekly_period_start=weekly_period_start,
            run_at=run_at,
        )
        if staged is None:
            skipped_auftraege.append(batch.auftragsnummer)
            continue
        if simulate_interruption_before_provider:
            raise HomeDemoControlledInterruption(
                staged.versand_id,
                staged.correlation_id,
            )
        message, incident = _send_staged_mock_versand(
            connection,
            settings,
            staged,
            run_at=run_at,
            simulate_timeout=simulate_timeout,
            simulate_crash_after_provider=simulate_crash_after_provider,
        )
        provider_call_count += 1
        if incident is not None:
            manual_review_incidents.append(incident)
        elif message is not None:
            messages.append(message)

    return MockVersandPreparationResult(
        ready_document_count=len(ready_documents),
        already_processed_count=already_processed_count,
        weekly_due=weekly_due,
        period_start=weekly_period_start,
        messages=tuple(messages),
        reused_message_paths=tuple(reused_message_paths),
        skipped_auftraege=tuple(skipped_auftraege),
        manual_review_incidents=tuple(manual_review_incidents),
        blocked_auftraege=tuple(blocked_auftraege),
        provider_call_count=provider_call_count,
    )


def _prepare_ready_mock_versand_locked(
    connection: Any,
    settings: Settings,
    *,
    run_at: datetime | None = None,
    simulate_timeout: bool = False,
    simulate_interruption_before_provider: bool = False,
    simulate_crash_after_provider: bool = False,
) -> MockVersandPreparationResult:
    _assert_demo_safety(settings)
    crash_incidents = _recover_ambiguous_sending_mock_versand(
        connection,
        settings,
    )
    ready_documents, already_processed_count = _load_ready_documents(
        connection,
        settings,
    )
    if crash_incidents:
        return MockVersandPreparationResult(
            ready_document_count=len(ready_documents),
            already_processed_count=already_processed_count,
            weekly_due=True if run_at is not None else None,
            period_start=(
                period_start(run_at.date()) if run_at is not None else None
            ),
            messages=(),
            reused_message_paths=(),
            skipped_auftraege=(),
            manual_review_incidents=crash_incidents,
            blocked_auftraege=tuple(
                incident.auftragsnummer for incident in crash_incidents
            ),
            provider_call_count=0,
        )
    staged_versand = _load_staged_mock_versand(connection, settings)
    if staged_versand:
        staged_periods = {
            staged.period_start
            for staged in staged_versand
            if staged.period_start is not None
        }
        if len(staged_periods) > 1:
            raise HomeDemoError(
                'plusieurs periodes PLANNED ne peuvent pas etre reprises ensemble'
            )
        messages: list[MockSendResult] = []
        incidents: list[MockManualReviewIncident] = []
        for staged in staged_versand:
            message, incident = _send_staged_mock_versand(
                connection,
                settings,
                staged,
                run_at=run_at,
                simulate_timeout=simulate_timeout,
                simulate_crash_after_provider=(
                    simulate_crash_after_provider
                ),
                resumed=True,
            )
            if message is not None:
                messages.append(message)
            if incident is not None:
                incidents.append(incident)
        staged_period = next(iter(staged_periods), None)
        return MockVersandPreparationResult(
            ready_document_count=len(ready_documents),
            already_processed_count=already_processed_count,
            weekly_due=True if run_at is not None else None,
            period_start=staged_period,
            messages=tuple(messages),
            reused_message_paths=(),
            skipped_auftraege=(),
            manual_review_incidents=tuple(incidents),
            resumed_versand_count=len(staged_versand),
            provider_call_count=len(staged_versand),
        )
    if run_at is not None:
        if run_at.tzinfo is None:
            raise HomeDemoError('la date simulee exige un fuseau horaire')
        current_period_start = period_start(run_at.date())
        cursor = connection.cursor()
        try:
            cursor.execute(
                '''
                SELECT MAX(period_start)
                FROM versand
                WHERE modus = 'AUTOMATIC'
                  AND status IN ('ACCEPTED', 'DELIVERED')
                  AND period_start IS NOT NULL
                '''
            )
            last_period_start = cursor.fetchone()[0]
            connection.commit()
        finally:
            cursor.close()
        due = is_weekly_run_due(
            run_at,
            settings.schedule.weekday,
            settings.schedule.time_of_day,
            last_period_start,
        )
        if not due:
            return MockVersandPreparationResult(
                ready_document_count=len(ready_documents),
                already_processed_count=already_processed_count,
                weekly_due=False,
                period_start=current_period_start,
                messages=(),
                reused_message_paths=(),
                skipped_auftraege=(),
            )
        return _prepare_mock_versand_documents(
            connection,
            settings,
            ready_documents,
            already_processed_count=already_processed_count,
            weekly_due=True,
            weekly_period_start=current_period_start,
            run_at=run_at,
            simulate_timeout=simulate_timeout,
            simulate_interruption_before_provider=(
                simulate_interruption_before_provider
            ),
            simulate_crash_after_provider=simulate_crash_after_provider,
        )
    return _prepare_mock_versand_documents(
        connection,
        settings,
        ready_documents,
        already_processed_count=already_processed_count,
        weekly_due=None,
        simulate_timeout=simulate_timeout,
        simulate_interruption_before_provider=(
            simulate_interruption_before_provider
        ),
        simulate_crash_after_provider=simulate_crash_after_provider,
    )


def prepare_ready_mock_versand(
    connection: Any,
    settings: Settings,
    *,
    run_at: datetime | None = None,
    simulate_timeout: bool = False,
    simulate_interruption_before_provider: bool = False,
    simulate_crash_after_provider: bool = False,
    lock_timeout_seconds: int = 0,
    test_hold_lock_seconds: float = 0,
) -> MockVersandPreparationResult:
    _assert_demo_safety(settings)
    if run_at is not None and run_at.tzinfo is None:
        raise HomeDemoError('la date simulee exige un fuseau horaire')
    if not 0 <= test_hold_lock_seconds <= 5:
        raise HomeDemoError(
            'test_hold_lock_seconds doit etre compris entre 0 et 5'
        )
    lock_cursor = connection.cursor()
    lock_acquired = False
    try:
        lock_cursor.execute(
            'SELECT GET_LOCK(%s, %s)',
            ('pruefversand:mock-versand', lock_timeout_seconds),
        )
        lock_row = lock_cursor.fetchone()
        lock_acquired = bool(lock_row and lock_row[0] == 1)
        if not lock_acquired:
            connection.commit()
            return MockVersandPreparationResult(
                ready_document_count=0,
                already_processed_count=0,
                weekly_due=None,
                period_start=(
                    period_start(run_at.date()) if run_at else None
                ),
                messages=(),
                reused_message_paths=(),
                skipped_auftraege=(),
                lock_acquired=False,
                skipped_due_to_lock=True,
            )
        if test_hold_lock_seconds:
            time.sleep(test_hold_lock_seconds)
        return _prepare_ready_mock_versand_locked(
            connection,
            settings,
            run_at=run_at,
            simulate_timeout=simulate_timeout,
            simulate_interruption_before_provider=(
                simulate_interruption_before_provider
            ),
            simulate_crash_after_provider=simulate_crash_after_provider,
        )
    finally:
        if lock_acquired:
            try:
                lock_cursor.execute(
                    'SELECT RELEASE_LOCK(%s)',
                    ('pruefversand:mock-versand',),
                )
                lock_cursor.fetchone()
            except Exception:
                pass
        lock_cursor.close()


def run_home_demo(
    connection: Any,
    settings: Settings,
) -> HomeDemoResult:
    _assert_demo_safety(settings)
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

    csv_import = import_baustellen_csv(
        connection,
        settings.project_root / 'sample_data/baustellen_example.csv',
    )
    demo_directory, pdf_path = _prepare_demo_pdf(settings.app.nas_path)
    first = scan_and_persist(
        connection,
        demo_directory,
        settings.app.filename_pattern,
    )
    second = scan_and_persist(
        connection,
        demo_directory,
        settings.app.filename_pattern,
    )

    ready_documents = tuple(
        ReadyDocument(
            path=item.path,
            auftragsnummer=item.auftragsnummer,
            sha256=item.observation.sha256,
            size_bytes=item.observation.size_bytes,
        )
        for item in second.report.items
        if item.status == ProtokollStatus.READY
        and item.auftragsnummer is not None
        and item.observation.sha256 is not None
    )
    if not ready_documents:
        raise HomeDemoError(
            'demo-home ne trouve aucun Pruefprotokoll READY'
        )

    preparation = _prepare_mock_versand_documents(
        connection,
        settings,
        ready_documents,
    )

    return HomeDemoResult(
        csv_import=csv_import,
        first_scan=first.report,
        second_scan=second.report,
        pdf_path=pdf_path,
        messages=preparation.messages,
        reused_message_paths=preparation.reused_message_paths,
        skipped_auftraege=preparation.skipped_auftraege,
    )
