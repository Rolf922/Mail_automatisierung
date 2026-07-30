from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import time
from typing import Any

from pruefversand.models import ProtokollStatus
from pruefversand.scanner import (
    FileObservation,
    ScanItem,
    ScanReport,
    scan_directory,
)


@dataclass(frozen=True, slots=True)
class ScanPersistenceResult:
    report: ScanReport
    inserted_protokolle: int
    updated_protokolle: int
    lock_acquired: bool = True
    skipped_due_to_lock: bool = False


def _canonical_path(path: Path) -> str:
    return str(path.resolve())


def _path_sha256(path: str) -> str:
    return hashlib.sha256(path.encode('utf-8')).hexdigest()


def _mysql_mtime(mtime_ns: int) -> datetime:
    return datetime.fromtimestamp(
        mtime_ns / 1_000_000_000,
        tz=timezone.utc,
    ).replace(tzinfo=None)


def load_previous_observations(
    connection: Any,
) -> dict[str, FileObservation]:
    cursor = connection.cursor()
    try:
        cursor.execute(
            '''
            SELECT nas_pfad, dateigroesse, datei_geaendert_ns, sha256
            FROM scan_observation
            '''
        )
        return {
            str(path): FileObservation(
                size_bytes=int(size),
                mtime_ns=int(mtime_ns),
                sha256=str(sha256) if sha256 is not None else None,
            )
            for path, size, mtime_ns, sha256 in cursor.fetchall()
        }
    finally:
        cursor.close()


def _resolve_auftrag_id(cursor: Any, auftragsnummer: str | None) -> int | None:
    if not auftragsnummer:
        return None
    cursor.execute(
        '''
        SELECT id
        FROM auftrag
        WHERE auftragsnummer = %s AND aktiv = TRUE
        ''',
        (auftragsnummer,),
    )
    row = cursor.fetchone()
    return int(row[0]) if row is not None else None


def _observation_state(
    cursor: Any,
    item: ScanItem,
    canonical_path: str,
) -> tuple[int, bool]:
    cursor.execute(
        '''
        SELECT dateigroesse, datei_geaendert_ns, sha256, stable_scan_count
        FROM scan_observation
        WHERE path_sha256 = %s
        FOR UPDATE
        ''',
        (_path_sha256(canonical_path),),
    )
    row = cursor.fetchone()
    if row is None:
        return 1, False

    same = (
        int(row[0]) == item.observation.size_bytes
        and int(row[1]) == item.observation.mtime_ns
        and row[2] == item.observation.sha256
    )
    if not same or item.status == ProtokollStatus.DISCOVERED:
        return 1, True
    return min(int(row[3]) + 1, 255), True


def _save_observation(
    cursor: Any,
    item: ScanItem,
    canonical_path: str,
    stable_scan_count: int,
    existed: bool,
) -> None:
    error = None if item.status == ProtokollStatus.READY else item.reason
    parameters = (
        canonical_path,
        item.observation.sha256,
        item.observation.size_bytes,
        item.observation.mtime_ns,
        stable_scan_count,
        item.status.value,
        error,
    )
    if existed:
        cursor.execute(
            '''
            UPDATE scan_observation
            SET nas_pfad = %s,
                sha256 = %s,
                dateigroesse = %s,
                datei_geaendert_ns = %s,
                stable_scan_count = %s,
                status = %s,
                letzte_fehler = %s,
                last_seen_at = CURRENT_TIMESTAMP(6)
            WHERE path_sha256 = %s
            ''',
            parameters + (_path_sha256(canonical_path),),
        )
    else:
        cursor.execute(
            '''
            INSERT INTO scan_observation
                (
                    path_sha256,
                    nas_pfad,
                    sha256,
                    dateigroesse,
                    datei_geaendert_ns,
                    stable_scan_count,
                    status,
                    letzte_fehler
                )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ''',
            (_path_sha256(canonical_path),) + parameters,
        )


def _persist_item(
    cursor: Any,
    item: ScanItem,
) -> tuple[ScanItem, bool, bool]:
    canonical_path = _canonical_path(item.path)
    stable_scan_count, observation_existed = _observation_state(
        cursor,
        item,
        canonical_path,
    )
    final_item = item
    if stable_scan_count < 2:
        final_item = replace(
            item,
            status=ProtokollStatus.DISCOVERED,
            reason='premiere observation ou fichier modifie',
        )

    auftrag_id = _resolve_auftrag_id(cursor, item.auftragsnummer)
    if (
        final_item.status == ProtokollStatus.READY
        and auftrag_id is None
    ):
        final_item = replace(
            final_item,
            status=ProtokollStatus.MANUAL_REVIEW,
            reason='Auftrag inconnu ou inactif',
        )

    existing_protokoll = None
    if item.observation.sha256 is not None:
        cursor.execute(
            '''
            SELECT id, nas_pfad, status
            FROM protokoll
            WHERE sha256 = %s
            FOR UPDATE
            ''',
            (item.observation.sha256,),
        )
        existing_protokoll = cursor.fetchone()
        if (
            existing_protokoll is not None
            and str(existing_protokoll[1]) != canonical_path
        ):
            final_item = replace(
                final_item,
                status=ProtokollStatus.MANUAL_REVIEW,
                reason='SHA-256 deja connu sous un autre chemin NAS',
            )

    if (
        existing_protokoll is not None
        and str(existing_protokoll[1]) == canonical_path
    ):
        cursor.execute(
            '''
            SELECT
                d.action,
                d.assigned_auftrag_id,
                a.auftragsnummer,
                d.reason
            FROM manual_review_decision AS d
            LEFT JOIN auftrag AS a ON a.id = d.assigned_auftrag_id
            WHERE d.protokoll_id = %s
            ''',
            (existing_protokoll[0],),
        )
        manual_decision = cursor.fetchone()
        if manual_decision is not None:
            if str(manual_decision[0]) == 'ASSIGN':
                auftrag_id = int(manual_decision[1])
                final_item = replace(
                    final_item,
                    status=ProtokollStatus.READY,
                    reason='Auftrag associe manuellement',
                    auftragsnummer=str(manual_decision[2]),
                )
            elif str(manual_decision[0]) == 'IGNORE':
                final_item = replace(
                    final_item,
                    status=ProtokollStatus.MANUAL_REVIEW,
                    reason=(
                        'Ignore manuellement : '
                        + str(manual_decision[3])
                    ),
                )

    _save_observation(
        cursor,
        final_item,
        canonical_path,
        stable_scan_count,
        observation_existed,
    )
    if item.observation.sha256 is None:
        return final_item, False, False

    error = (
        None
        if final_item.status == ProtokollStatus.READY
        else final_item.reason
    )
    if existing_protokoll is None:
        cursor.execute(
            '''
            INSERT INTO protokoll
                (
                    auftrag_id,
                    dateiname,
                    nas_pfad,
                    sha256,
                    dateigroesse,
                    datei_geaendert_at,
                    stable_scan_count,
                    status,
                    letzte_fehler
                )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''',
            (
                auftrag_id,
                item.path.name,
                canonical_path,
                item.observation.sha256,
                item.observation.size_bytes,
                _mysql_mtime(item.observation.mtime_ns),
                stable_scan_count,
                final_item.status.value,
                error,
            ),
        )
        return final_item, True, False

    if str(existing_protokoll[1]) != canonical_path:
        return final_item, False, False

    protected_statuses = {
        ProtokollStatus.SENDING.value,
        ProtokollStatus.ACCEPTED.value,
        ProtokollStatus.MANUALLY_SENT.value,
    }
    database_status = (
        str(existing_protokoll[2])
        if str(existing_protokoll[2]) in protected_statuses
        else final_item.status.value
    )
    cursor.execute(
        '''
        UPDATE protokoll
        SET auftrag_id = %s,
            dateiname = %s,
            dateigroesse = %s,
            datei_geaendert_at = %s,
            last_seen_at = CURRENT_TIMESTAMP(6),
            stable_scan_count = %s,
            status = %s,
            letzte_fehler = %s
        WHERE id = %s
        ''',
        (
            auftrag_id,
            item.path.name,
            item.observation.size_bytes,
            _mysql_mtime(item.observation.mtime_ns),
            stable_scan_count,
            database_status,
            error,
            existing_protokoll[0],
        ),
    )
    return final_item, False, True


def scan_and_persist(
    connection: Any,
    nas_path: str | Path,
    filename_pattern: str,
    *,
    lock_timeout_seconds: int = 0,
    test_hold_lock_seconds: float = 0,
) -> ScanPersistenceResult:
    lock_cursor = connection.cursor()
    lock_acquired = False
    try:
        lock_cursor.execute(
            'SELECT GET_LOCK(%s, %s)',
            ('pruefversand:nas-scan', lock_timeout_seconds),
        )
        lock_row = lock_cursor.fetchone()
        lock_acquired = bool(lock_row and lock_row[0] == 1)
        if not lock_acquired:
            connection.commit()
            return ScanPersistenceResult(
                report=ScanReport(items=(), observations={}),
                inserted_protokolle=0,
                updated_protokolle=0,
                lock_acquired=False,
                skipped_due_to_lock=True,
            )
        if test_hold_lock_seconds:
            time.sleep(test_hold_lock_seconds)

        previous = load_previous_observations(connection)
        connection.commit()
        report = scan_directory(
            nas_path,
            filename_pattern,
            previous,
        )

        connection.start_transaction()
        cursor = connection.cursor()
        try:
            final_items: list[ScanItem] = []
            inserted = 0
            updated = 0
            for item in report.items:
                final_item, was_inserted, was_updated = _persist_item(
                    cursor,
                    item,
                )
                final_items.append(final_item)
                inserted += int(was_inserted)
                updated += int(was_updated)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()

        return ScanPersistenceResult(
            report=ScanReport(
                items=tuple(final_items),
                observations=report.observations,
            ),
            inserted_protokolle=inserted,
            updated_protokolle=updated,
            lock_acquired=True,
            skipped_due_to_lock=False,
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        if lock_acquired:
            try:
                lock_cursor.execute(
                    'SELECT RELEASE_LOCK(%s)',
                    ('pruefversand:nas-scan',),
                )
                lock_cursor.fetchone()
            except Exception:
                pass
        lock_cursor.close()
