from __future__ import annotations

from dataclasses import dataclass
from contextlib import suppress
import hashlib
import importlib.util
import os
from pathlib import Path
from typing import Any

from pruefversand.config import DatabaseSettings
from pruefversand.csv_records import (
    BaustelleRecord,
    CsvIssue,
    CsvValidationResult,
    validate_csv,
)


class DatabaseUnavailable(RuntimeError):
    pass


MYSQL_CONNECTION_TIMEOUT_SECONDS = 5


@dataclass(frozen=True, slots=True)
class MySqlProbeResult:
    server_version: str


class CsvImportRejected(ValueError):
    def __init__(self, validation: CsvValidationResult) -> None:
        self.validation = validation
        details = '; '.join(
            f'ligne {issue.line_number}, {issue.field}: {issue.message}'
            for issue in validation.issues
        )
        super().__init__(f'CSV refuse avant transaction : {details}')


@dataclass(frozen=True, slots=True)
class MigrationResult:
    statements_executed: int


@dataclass(frozen=True, slots=True)
class CsvImportResult:
    csv_sha256: str
    total_rows: int
    inserted_auftraege: int
    updated_auftraege: int
    duplicate_import: bool
    deleted_auftraege: int = 0
    deactivated_auftraege: int = 0


def mysql_driver_available() -> bool:
    try:
        return importlib.util.find_spec("mysql.connector") is not None
    except ModuleNotFoundError:
        return False


def connect_mysql(settings: DatabaseSettings) -> Any:
    if not mysql_driver_available():
        raise DatabaseUnavailable(
            "mysql-connector-python n'est pas installé ; "
            "installer l'option '.[mysql]' dans un environnement autorisé"
        )

    password = os.environ.get(settings.password_env)
    if not password:
        raise DatabaseUnavailable(
            f"Variable de secret absente : {settings.password_env}"
        )

    import mysql.connector  # type: ignore[import-not-found]

    arguments: dict[str, object] = {
        "host": settings.host,
        "port": settings.port,
        "database": settings.name,
        "user": settings.user,
        "password": password,
        "autocommit": False,
        "connection_timeout": MYSQL_CONNECTION_TIMEOUT_SECONDS,
    }
    if settings.require_tls:
        arguments["ssl_disabled"] = False
    try:
        return mysql.connector.connect(**arguments)
    except mysql.connector.Error as exc:
        raise DatabaseUnavailable('Connexion MySQL impossible') from exc


def probe_mysql(settings: DatabaseSettings) -> MySqlProbeResult:
    """Perform a bounded, read-only MySQL availability probe."""
    connection = connect_mysql(settings)
    cursor = None
    try:
        cursor = connection.cursor()
        cursor.execute('SELECT VERSION()')
        row = cursor.fetchone()
        if not row or not row[0]:
            raise DatabaseUnavailable('Réponse MySQL invalide')
        return MySqlProbeResult(server_version=str(row[0]))
    except DatabaseUnavailable:
        raise
    except Exception as exc:
        raise DatabaseUnavailable('Contrôle MySQL impossible') from exc
    finally:
        if cursor is not None:
            with suppress(Exception):
                cursor.close()
        with suppress(Exception):
            connection.rollback()
        with suppress(Exception):
            connection.close()


def _migration_statements(script: str) -> tuple[str, ...]:
    '''Split the controlled project migration into executable statements.'''
    return tuple(
        statement.strip()
        for statement in script.split(';')
        if statement.strip()
    )


def apply_migration(connection: Any, path: str | Path) -> MigrationResult:
    '''Apply an idempotent project migration to a MySQL connection.

    MySQL commits DDL implicitly. The migration is intentionally idempotent
    because it cannot be made atomic through a surrounding transaction.
    '''
    source = Path(path)
    script = source.read_text(encoding='utf-8')
    statements = _migration_statements(script)
    cursor = connection.cursor()
    try:
        for statement in statements:
            cursor.execute(statement)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
    return MigrationResult(statements_executed=len(statements))


def _records_by_auftrag(
    records: tuple[BaustelleRecord, ...],
) -> dict[str, tuple[BaustelleRecord, ...]]:
    grouped: dict[str, list[BaustelleRecord]] = {}
    for record in records:
        grouped.setdefault(record.auftragsnummer, []).append(record)

    issues: list[CsvIssue] = []
    for auftragsnummer, group in grouped.items():
        identity = {(item.baustelle_name, item.kunde_name) for item in group}
        if len(identity) > 1:
            issues.append(
                CsvIssue(
                    0,
                    'auftragsnummer',
                    (
                        f'Auftrag {auftragsnummer}: Baustelle ou Kunde '
                        'incoherent dans le meme CSV'
                    ),
                )
            )
    if issues:
        raise CsvImportRejected(
            CsvValidationResult(
                records=records,
                issues=tuple(issues),
                total_rows=len(records),
            )
        )
    return {key: tuple(value) for key, value in grouped.items()}


def _upsert_auftrag(
    cursor: Any,
    auftragsnummer: str,
    records: tuple[BaustelleRecord, ...],
) -> tuple[bool, bool]:
    first = records[0]
    auftrag_active = any(record.aktiv for record in records)
    cursor.execute(
        '''
        SELECT a.id, a.baustelle_id, a.aktiv
        FROM auftrag AS a
        WHERE a.auftragsnummer = %s
        FOR UPDATE
        ''',
        (auftragsnummer,),
    )
    existing = cursor.fetchone()
    if existing is None:
        cursor.execute(
            '''
            INSERT INTO baustelle (name, kunde_name, aktiv)
            VALUES (%s, %s, %s)
            ''',
            (first.baustelle_name, first.kunde_name, auftrag_active),
        )
        baustelle_id = cursor.lastrowid
        cursor.execute(
            '''
            INSERT INTO auftrag
                (auftragsnummer, baustelle_id, aktiv)
            VALUES (%s, %s, %s)
            ''',
            (auftragsnummer, baustelle_id, auftrag_active),
        )
        auftrag_id = cursor.lastrowid
        inserted = True
        deactivated = False
    else:
        auftrag_id, baustelle_id, was_active = existing
        cursor.execute(
            '''
            UPDATE baustelle
            SET name = %s, kunde_name = %s, aktiv = %s
            WHERE id = %s
            ''',
            (
                first.baustelle_name,
                first.kunde_name,
                auftrag_active,
                baustelle_id,
            ),
        )
        cursor.execute(
            'UPDATE auftrag SET aktiv = %s WHERE id = %s',
            (auftrag_active, auftrag_id),
        )
        inserted = False
        deactivated = bool(was_active) and not auftrag_active

    for record in records:
        cursor.execute(
            '''
            SELECT id
            FROM empfaenger
            WHERE auftrag_id = %s
              AND email = %s
              AND empfaengertyp = %s
            FOR UPDATE
            ''',
            (auftrag_id, record.email, record.empfaengertyp),
        )
        recipient = cursor.fetchone()
        if recipient is None:
            cursor.execute(
                '''
                INSERT INTO empfaenger
                    (
                        auftrag_id,
                        kontakt_name,
                        email,
                        empfaengertyp,
                        aktiv
                    )
                VALUES (%s, %s, %s, %s, %s)
                ''',
                (
                    auftrag_id,
                    record.kontakt_name or None,
                    record.email,
                    record.empfaengertyp,
                    record.aktiv,
                ),
            )
        else:
            cursor.execute(
                '''
                UPDATE empfaenger
                SET kontakt_name = %s, aktiv = %s
                WHERE id = %s
                ''',
                (
                    record.kontakt_name or None,
                    record.aktiv,
                    recipient[0],
                ),
            )
    return inserted, deactivated


def import_baustellen_csv(
    connection: Any,
    path: str | Path,
    *,
    lock_timeout_seconds: int = 5,
) -> CsvImportResult:
    '''Validate then transactionally upsert a synthetic or approved CSV.

    Recipients absent from a later CSV are deliberately left unchanged.
    '''
    source = Path(path)
    validation = validate_csv(source)
    if not validation.is_valid:
        raise CsvImportRejected(validation)

    grouped = _records_by_auftrag(validation.records)
    csv_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    cursor = connection.cursor()
    lock_acquired = False
    try:
        cursor.execute(
            'SELECT GET_LOCK(%s, %s)',
            ('pruefversand:csv-import', lock_timeout_seconds),
        )
        lock_row = cursor.fetchone()
        lock_acquired = bool(lock_row and lock_row[0] == 1)
        if not lock_acquired:
            raise TimeoutError('verrou MySQL import CSV indisponible')

        connection.start_transaction()
        cursor.execute(
            'SELECT status FROM csv_import WHERE sha256 = %s FOR UPDATE',
            (csv_sha256,),
        )
        previous_import = cursor.fetchone()
        if previous_import is not None:
            connection.commit()
            return CsvImportResult(
                csv_sha256=csv_sha256,
                total_rows=validation.total_rows,
                inserted_auftraege=0,
                updated_auftraege=0,
                duplicate_import=True,
            )

        inserted = 0
        updated = 0
        deactivated = 0
        for auftragsnummer in sorted(grouped):
            was_inserted, was_deactivated = _upsert_auftrag(
                cursor,
                auftragsnummer,
                grouped[auftragsnummer],
            )
            if was_inserted:
                inserted += 1
            else:
                updated += 1
            deactivated += int(was_deactivated)

        cursor.execute(
            '''
            INSERT INTO csv_import
                (
                    dateiname,
                    sha256,
                    status,
                    zeilen_gesamt,
                    eingefuegt,
                    aktualisiert,
                    abgelehnt
                )
            VALUES (%s, %s, 'IMPORTED', %s, %s, %s, 0)
            ''',
            (
                source.name,
                csv_sha256,
                validation.total_rows,
                inserted,
                updated,
            ),
        )
        connection.commit()
        return CsvImportResult(
            csv_sha256=csv_sha256,
            total_rows=validation.total_rows,
            inserted_auftraege=inserted,
            updated_auftraege=updated,
            duplicate_import=False,
            deactivated_auftraege=deactivated,
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        if lock_acquired:
            try:
                cursor.execute(
                    'SELECT RELEASE_LOCK(%s)',
                    ('pruefversand:csv-import',),
                )
                cursor.fetchone()
            except Exception:
                pass
        cursor.close()
