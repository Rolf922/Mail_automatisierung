from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

from pruefversand import __version__
from pruefversand.config import ConfigError, Settings, load_settings
from pruefversand.csv_records import validate_csv
from pruefversand.database import (
    CsvImportRejected,
    DatabaseUnavailable,
    MYSQL_CONNECTION_TIMEOUT_SECONDS,
    connect_mysql,
    import_baustellen_csv,
    mysql_driver_available,
    probe_mysql,
)
from pruefversand.home_demo import (
    HomeDemoError,
    HomeDemoSafetyError,
    prepare_ready_mock_versand,
    run_home_demo,
    run_manual_review_demo,
)
from pruefversand.manual_review import (
    ManualReviewError,
    ManualReviewSafetyError,
    assert_manual_review_safety,
    assign_manual_review,
    ignore_manual_review,
    list_manual_reviews,
)
from pruefversand.manual_versand import (
    ManualVersandError,
    ManualVersandSafetyError,
    assert_manual_versand_safety,
    record_manual_urgent_versand,
)
from pruefversand.mysql_scanner import scan_and_persist
from pruefversand.pdf_tools import inspect_pdf
from pruefversand.versand_review import (
    VersandReviewSafetyError,
    assert_versand_review_safety,
    list_versand_review_incidents,
)
from pruefversand.versand_resolution import (
    VersandResolutionError,
    confirm_provider_accepted,
    keep_provider_result_unknown,
    retry_after_provider_not_accepted,
)
from pruefversand.scanner import scan_directory


def _settings_payload(settings: Settings) -> dict[str, object]:
    mysql_available = mysql_driver_available()
    paths_ok = settings.app.nas_path.is_dir()
    home_safe = not (
        settings.app.environment in {"home", "test"}
        and (
            not settings.app.dry_run
            or settings.mail.provider != "mock"
        )
    )
    return {
        "environment": settings.app.environment,
        "dry_run": settings.app.dry_run,
        "mail_provider": settings.mail.provider,
        "nas_path": str(settings.app.nas_path),
        "nas_path_exists": paths_ok,
        "mysql_driver_available": mysql_available,
        "home_safety_lock": home_safe,
    }


def _healthcheck(settings: Settings, _: argparse.Namespace) -> int:
    payload = _settings_payload(settings)
    errors: list[dict[str, str]] = []
    if not payload['nas_path_exists']:
        errors.append(
            {
                'code': 'NAS_UNAVAILABLE',
                'message': f'Dossier NAS introuvable : {settings.app.nas_path}',
            }
        )
    if not payload['home_safety_lock']:
        errors.append(
            {
                'code': 'HOME_SAFETY_INVALID',
                'message': 'Configuration HOME non sûre',
            }
        )

    mysql_status = 'DRIVER_UNAVAILABLE'
    mysql_server_version = None
    if not payload['mysql_driver_available']:
        errors.append(
            {
                'code': 'MYSQL_DRIVER_UNAVAILABLE',
                'message': 'Connecteur MySQL indisponible',
            }
        )
    else:
        try:
            probe = probe_mysql(settings.database)
        except DatabaseUnavailable:
            mysql_status = 'UNAVAILABLE'
            errors.append(
                {
                    'code': 'MYSQL_UNAVAILABLE',
                    'message': 'Connexion MySQL indisponible',
                }
            )
        else:
            mysql_status = 'AVAILABLE'
            mysql_server_version = probe.server_version

    payload.update(
        {
            'nas_status': (
                'AVAILABLE' if payload['nas_path_exists'] else 'UNAVAILABLE'
            ),
            'mysql_status': mysql_status,
            'mysql_server_version': mysql_server_version,
            'mysql_connection_timeout_seconds': (
                MYSQL_CONNECTION_TIMEOUT_SECONDS
            ),
            'database_checked': True,
            'errors': errors,
            'status': 'READY' if not errors else 'NOT_READY',
        }
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload['status'] == 'READY' else 1


def _validate_csv(settings: Settings, args: argparse.Namespace) -> int:
    del settings
    result = validate_csv(args.path)
    payload = {
        "file": str(Path(args.path)),
        "valid": result.is_valid,
        "total_rows": result.total_rows,
        "valid_rows": len(result.records),
        "issues": [
            {
                "line": issue.line_number,
                "field": issue.field,
                "message": issue.message,
            }
            for issue in result.issues
        ],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if result.is_valid else 2


def _import_csv(settings: Settings, args: argparse.Namespace) -> int:
    connection = connect_mysql(settings.database)
    try:
        result = import_baustellen_csv(connection, args.path)
    finally:
        connection.close()
    payload = {
        'file': str(Path(args.path)),
        'status': 'ALREADY_IMPORTED' if result.duplicate_import else 'IMPORTED',
        'total_rows': result.total_rows,
        'inserted_auftraege': result.inserted_auftraege,
        'updated_auftraege': result.updated_auftraege,
        'deleted_auftraege': result.deleted_auftraege,
        'deactivated_auftraege': result.deactivated_auftraege,
        'duplicate_import': result.duplicate_import,
        'csv_sha256': result.csv_sha256,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _scan(settings: Settings, args: argparse.Namespace) -> int:
    scan_path = settings.app.nas_path
    if args.path:
        candidate = Path(args.path)
        if not candidate.is_absolute():
            candidate = settings.project_root / candidate
        scan_path = candidate.resolve()
        if settings.app.environment in {'home', 'test'}:
            try:
                scan_path.relative_to(settings.app.nas_path.resolve())
            except ValueError as exc:
                raise ConfigError(
                    'scan --path doit rester dans le NAS HOME simule'
                ) from exc
    inserted = 0
    updated = 0
    if args.dry_run:
        if args.test_hold_lock_seconds:
            raise ConfigError(
                '--test-hold-lock-seconds exige un scan MySQL persistant'
            )
        mode = 'dry-run'
        report = scan_directory(
            scan_path,
            settings.app.filename_pattern,
        )
    else:
        if not 0 <= args.test_hold_lock_seconds <= 5:
            raise ConfigError(
                '--test-hold-lock-seconds doit etre compris entre 0 et 5'
            )
        if args.test_hold_lock_seconds and settings.app.environment not in {
            'home',
            'test',
        }:
            raise ConfigError(
                '--test-hold-lock-seconds est reserve a HOME/TEST'
            )
        mode = 'mysql'
        connection = connect_mysql(settings.database)
        try:
            result = scan_and_persist(
                connection,
                scan_path,
                settings.app.filename_pattern,
                test_hold_lock_seconds=args.test_hold_lock_seconds,
            )
        finally:
            connection.close()
        report = result.report
        inserted = result.inserted_protokolle
        updated = result.updated_protokolle
    lock_acquired = True if args.dry_run else result.lock_acquired
    skipped_due_to_lock = (
        False if args.dry_run else result.skipped_due_to_lock
    )
    payload = {
        "status": (
            "SKIPPED_LOCK_BUSY"
            if skipped_due_to_lock
            else "DRY_RUN_COMPLETE_NO_PERSISTENCE"
            if args.dry_run
            else "SCAN_COMPLETE"
        ),
        "nas_path": str(scan_path),
        "mode": mode,
        "dry_run": settings.app.dry_run,
        "mail_provider": settings.mail.provider,
        "database_checked": not args.dry_run,
        "persistence_performed": not args.dry_run,
        "inserted_protokolle": inserted,
        "updated_protokolle": updated,
        "lock_acquired": lock_acquired,
        "skipped_due_to_lock": skipped_due_to_lock,
        "files": [
            {
                "name": item.path.name,
                "status": item.status,
                "reason": item.reason,
                "auftragsnummer": item.auftragsnummer,
                "size_bytes": item.observation.size_bytes,
                "sha256": item.observation.sha256,
            }
            for item in report.items
        ],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _demo_home(settings: Settings, _: argparse.Namespace) -> int:
    connection = connect_mysql(settings.database)
    try:
        result = run_home_demo(connection, settings)
    finally:
        connection.close()
    payload = {
        'status': 'DEMO_CREATED',
        'dry_run': settings.app.dry_run,
        'mail_provider': settings.mail.provider,
        'mysql_host': settings.database.host,
        'mysql_port': settings.database.port,
        'csv_status': (
            'ALREADY_IMPORTED'
            if result.csv_import.duplicate_import
            else 'IMPORTED'
        ),
        'first_scan': [
            {
                'name': item.path.name,
                'status': item.status,
            }
            for item in result.first_scan.items
        ],
        'second_scan': [
            {
                'name': item.path.name,
                'status': item.status,
            }
            for item in result.second_scan.items
        ],
        'pdf_path': str(result.pdf_path.resolve()),
        'messages': [
            {
                'eml_path': str(message.message_path.resolve()),
                'correlation_id': message.correlation_id,
                'recipients': message.actual_recipients,
            }
            for message in result.messages
        ],
        'reused_message_paths': [
            str(path.resolve())
            for path in result.reused_message_paths
        ],
        'skipped_auftraege': result.skipped_auftraege,
        'new_message_count': len(result.messages),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _demo_manual_review_home(
    settings: Settings,
    _: argparse.Namespace,
) -> int:
    connection = connect_mysql(settings.database)
    try:
        result = run_manual_review_demo(connection, settings)
    finally:
        connection.close()
    payload = {
        'status': 'MANUAL_REVIEW_DEMO_COMPLETE',
        'dry_run': settings.app.dry_run,
        'mail_provider': settings.mail.provider,
        'mysql_host': settings.database.host,
        'mysql_port': settings.database.port,
        'csv_status': (
            'ALREADY_IMPORTED'
            if result.csv_import.duplicate_import
            else 'IMPORTED'
        ),
        'first_scan': [
            {
                'name': item.path.name,
                'status': item.status,
                'reason': item.reason,
                'auftragsnummer': item.auftragsnummer,
            }
            for item in result.first_scan.items
        ],
        'second_scan': [
            {
                'name': item.path.name,
                'status': item.status,
                'reason': item.reason,
                'auftragsnummer': item.auftragsnummer,
            }
            for item in result.second_scan.items
        ],
        'pdf_paths': [
            str(path.resolve())
            for path in result.pdf_paths
        ],
        'versand_count_before': result.versand_count_before,
        'versand_count_after': result.versand_count_after,
        'eml_count_before': result.eml_count_before,
        'eml_count_after': result.eml_count_after,
        'new_message_count': 0,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _prepare_mock_versand(
    settings: Settings,
    args: argparse.Namespace,
) -> int:
    run_at = None
    if args.at:
        try:
            run_at = datetime.fromisoformat(
                args.at.replace('Z', '+00:00')
            )
        except ValueError as exc:
            raise ConfigError(
                '--at doit etre une date ISO-8601 valide'
            ) from exc
        if run_at.tzinfo is None:
            raise ConfigError(
                '--at exige un decalage UTC explicite, par exemple +02:00'
            )
    if not 0 <= args.test_hold_lock_seconds <= 5:
        raise ConfigError(
            '--test-hold-lock-seconds doit etre compris entre 0 et 5'
        )
    if args.test_hold_lock_seconds and settings.app.environment not in {
        'home',
        'test',
    }:
        raise ConfigError(
            '--test-hold-lock-seconds est reserve a HOME/TEST'
        )
    simulation_count = sum(
        (
            args.simulate_timeout,
            args.simulate_interruption_before_provider,
            args.simulate_crash_after_provider,
        )
    )
    if simulation_count > 1:
        raise ConfigError(
            'une seule simulation fournisseur peut etre active a la fois'
        )
    connection = connect_mysql(settings.database)
    try:
        result = prepare_ready_mock_versand(
            connection,
            settings,
            run_at=run_at,
            simulate_timeout=args.simulate_timeout,
            simulate_interruption_before_provider=(
                args.simulate_interruption_before_provider
            ),
            simulate_crash_after_provider=(
                args.simulate_crash_after_provider
            ),
            test_hold_lock_seconds=args.test_hold_lock_seconds,
        )
    finally:
        connection.close()
    payload = {
        'status': (
            'SKIPPED_LOCK_BUSY'
            if result.skipped_due_to_lock
            else 'MOCK_VERSAND_PREPARED'
        ),
        'dry_run': settings.app.dry_run,
        'mail_provider': settings.mail.provider,
        'simulated_at': run_at.isoformat() if run_at else None,
        'weekly_due': result.weekly_due,
        'period_start': (
            result.period_start.isoformat()
            if result.period_start
            else None
        ),
        'ready_document_count': result.ready_document_count,
        'already_processed_count': result.already_processed_count,
        'messages': [
            {
                'eml_path': str(message.message_path.resolve()),
                'correlation_id': message.correlation_id,
                'recipients': message.actual_recipients,
            }
            for message in result.messages
        ],
        'reused_message_paths': [
            str(path.resolve())
            for path in result.reused_message_paths
        ],
        'skipped_auftraege': result.skipped_auftraege,
        'blocked_auftraege': tuple(
            getattr(result, 'blocked_auftraege', ())
        ),
        'new_message_count': len(result.messages),
        'accepted_message_count': len(result.messages),
        'eml_written_count': len(result.messages),
        'resumed_versand_count': result.resumed_versand_count,
        'provider_call_count': result.provider_call_count,
        'mock_adapter_invocation_count': result.provider_call_count,
        'lock_acquired': result.lock_acquired,
        'skipped_due_to_lock': result.skipped_due_to_lock,
        'manual_review_incident_count': len(
            result.manual_review_incidents
        ),
        'manual_review_incidents': [
            {
                'versand_id': incident.versand_id,
                'correlation_id': incident.correlation_id,
                'auftragsnummer': incident.auftragsnummer,
                'reason': incident.reason,
                'incident_type': incident.incident_type,
                'total_attachment_bytes': (
                    incident.total_attachment_bytes
                ),
                'maximum_attachment_bytes': (
                    incident.maximum_attachment_bytes
                ),
                'provider_invoked': incident.provider_invoked,
                'message_accepted': incident.message_accepted,
                'eml_created': incident.eml_created,
                'automatic_retry_blocked': (
                    incident.automatic_retry_blocked
                ),
            }
            for incident in result.manual_review_incidents
        ],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _manual_review_list(
    settings: Settings,
    _: argparse.Namespace,
) -> int:
    assert_manual_review_safety(settings)
    connection = connect_mysql(settings.database)
    try:
        items = list_manual_reviews(connection, settings)
    finally:
        connection.close()
    payload = {
        'status': 'OK',
        'dry_run': settings.app.dry_run,
        'mail_provider': settings.mail.provider,
        'count': len(items),
        'items': [
            {
                'id': item.protokoll_id,
                'name': item.filename,
                'reason': item.reason,
                'stable_scan_count': item.stable_scan_count,
            }
            for item in items
        ],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _versand_review_list(
    settings: Settings,
    _: argparse.Namespace,
) -> int:
    assert_versand_review_safety(settings)
    connection = connect_mysql(settings.database)
    try:
        incidents = list_versand_review_incidents(connection, settings)
    finally:
        connection.close()
    payload = {
        'status': 'OK',
        'review_type': 'VERSAND',
        'read_only': True,
        'dry_run': settings.app.dry_run,
        'mail_provider': settings.mail.provider,
        'count': len(incidents),
        'items': [
            {
                'versand_id': incident.versand_id,
                'auftragsnummer': incident.auftragsnummer,
                'reason': incident.reason,
                'incident_at': incident.incident_at.isoformat(
                    sep=' ',
                    timespec='seconds',
                ) + 'Z',
                'linked_document_count': (
                    incident.linked_document_count
                ),
                'linked_documents': [
                    {
                        'protokoll_id': document.protokoll_id,
                        'name': document.filename,
                    }
                    for document in incident.linked_documents
                ],
                'automatic_retry_blocked': (
                    incident.automatic_retry_blocked
                ),
            }
            for incident in incidents
        ],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _versand_review_resolve(
    settings: Settings,
    args: argparse.Namespace,
) -> int:
    assert_versand_review_safety(settings)
    labels = {
        'PROVIDER_ACCEPTED': 'confirmer ACCEPTED',
        'PROVIDER_NOT_ACCEPTED_RETRY': (
            'confirmer non accepte et lancer le retry mock manuel unique'
        ),
        'KEEP_UNKNOWN': 'conserver MANUAL_REVIEW et journaliser inconnu',
    }
    if not _confirm_manual_review(
        f'Versand {args.versand_id} : {labels[args.decision]} ?'
    ):
        print(
            json.dumps(
                {
                    'status': 'CANCELLED',
                    'review_type': 'VERSAND',
                    'decision': args.decision,
                    'versand_id': args.versand_id,
                    'modified': False,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0
    resolvers = {
        'PROVIDER_ACCEPTED': confirm_provider_accepted,
        'PROVIDER_NOT_ACCEPTED_RETRY': (
            retry_after_provider_not_accepted
        ),
        'KEEP_UNKNOWN': keep_provider_result_unknown,
    }
    connection = connect_mysql(settings.database)
    try:
        result = resolvers[args.decision](
            connection,
            settings,
            args.versand_id,
            args.reason,
        )
    finally:
        connection.close()
    print(
        json.dumps(
            {
                'status': result.versand_status,
                'review_type': 'VERSAND',
                'decision_id': result.decision_id,
                'decision': result.decision,
                'versand_id': result.versand_id,
                'auftragsnummer': result.auftragsnummer,
                'reason': result.reason,
                'decided_at': result.decided_at.isoformat(),
                'retry_performed': result.retry_performed,
                'eml_path': (
                    str(result.message_path.resolve())
                    if result.message_path
                    else None
                ),
                'new_message_count': (
                    1 if result.message_path else 0
                ),
                'dry_run': settings.app.dry_run,
                'mail_provider': settings.mail.provider,
                'real_email_sent': False,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def _confirm_manual_review(prompt: str) -> bool:
    try:
        response = input(f'{prompt}\nSaisir OUI pour confirmer : ')
    except EOFError:
        return False
    return response.strip().upper() == 'OUI'


def _manual_versand_record(
    settings: Settings,
    args: argparse.Namespace,
) -> int:
    assert_manual_versand_safety(settings)
    sent_at = None
    if args.at:
        try:
            sent_at = datetime.fromisoformat(
                args.at.replace('Z', '+00:00')
            )
        except ValueError as exc:
            raise ConfigError(
                '--at doit etre une date ISO-8601 valide'
            ) from exc
        if sent_at.tzinfo is None:
            raise ConfigError(
                '--at exige un decalage UTC explicite, par exemple +02:00'
            )
    if not _confirm_manual_review(
        'Enregistrer le Pruefprotokoll '
        f'{args.protokoll_id} comme Versand urgent manuel ?'
    ):
        print(
            json.dumps(
                {
                    'status': 'CANCELLED',
                    'action': 'MANUAL_URGENT_VERSAND',
                    'protokoll_id': args.protokoll_id,
                    'modified': False,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0
    connection = connect_mysql(settings.database)
    try:
        result = record_manual_urgent_versand(
            connection,
            settings,
            args.protokoll_id,
            args.operator,
            args.reason,
            sent_at=sent_at,
        )
    finally:
        connection.close()
    print(
        json.dumps(
            {
                'status': result.protokoll_status,
                'action': 'MANUAL_URGENT_VERSAND',
                'versand_id': result.versand_id,
                'protokoll_id': result.protokoll_id,
                'name': result.filename,
                'sha256': result.sha256,
                'auftragsnummer': result.auftragsnummer,
                'operator': result.operator,
                'reason': result.reason,
                'sent_at': result.sent_at.isoformat(),
                'recipients': result.recipients,
                'eml_created': False,
                'dry_run': settings.app.dry_run,
                'mail_provider': settings.mail.provider,
                'real_email_sent': False,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def _manual_review_assign(
    settings: Settings,
    args: argparse.Namespace,
) -> int:
    assert_manual_review_safety(settings)
    if not _confirm_manual_review(
        'Associer le Pruefprotokoll '
        f'{args.protokoll_id} a l Auftrag {args.auftragsnummer} ?'
    ):
        print(
            json.dumps(
                {
                    'status': 'CANCELLED',
                    'action': 'ASSIGN',
                    'protokoll_id': args.protokoll_id,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0
    connection = connect_mysql(settings.database)
    try:
        result = assign_manual_review(
            connection,
            settings,
            args.protokoll_id,
            args.auftragsnummer,
            args.reason,
        )
    finally:
        connection.close()
    print(
        json.dumps(
            {
                'status': result.protokoll_status,
                'action': result.action,
                'decision_id': result.decision_id,
                'protokoll_id': result.protokoll_id,
                'name': result.filename,
                'auftragsnummer': result.auftragsnummer,
                'reason': result.reason,
                'decided_at': result.decided_at.isoformat(),
                'versand_prepared': False,
                'dry_run': settings.app.dry_run,
                'mail_provider': settings.mail.provider,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def _manual_review_ignore(
    settings: Settings,
    args: argparse.Namespace,
) -> int:
    assert_manual_review_safety(settings)
    if not _confirm_manual_review(
        f'Ignorer le Pruefprotokoll {args.protokoll_id} sans le supprimer ?'
    ):
        print(
            json.dumps(
                {
                    'status': 'CANCELLED',
                    'action': 'IGNORE',
                    'protokoll_id': args.protokoll_id,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0
    connection = connect_mysql(settings.database)
    try:
        result = ignore_manual_review(
            connection,
            settings,
            args.protokoll_id,
            args.reason,
        )
    finally:
        connection.close()
    print(
        json.dumps(
            {
                'status': 'IGNORED',
                'action': result.action,
                'decision_id': result.decision_id,
                'protokoll_id': result.protokoll_id,
                'name': result.filename,
                'reason': result.reason,
                'decided_at': result.decided_at.isoformat(),
                'file_deleted': False,
                'versand_prepared': False,
                'dry_run': settings.app.dry_run,
                'mail_provider': settings.mail.provider,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def _inspect_pdf(settings: Settings, args: argparse.Namespace) -> int:
    inspection = inspect_pdf(args.path, settings.app.filename_pattern)
    print(
        json.dumps(
            {
                "file": str(Path(args.path)),
                "valid_pdf": inspection.is_valid,
                "reason": inspection.reason,
                "auftragsnummer": inspection.auftragsnummer,
                "sha256": inspection.sha256,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    if not inspection.is_valid:
        return 2
    return 0 if inspection.auftragsnummer else 3


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pruefversand",
        description="Automatisation simple des Prüfprotokolle",
    )
    parser.add_argument(
        "--config",
        default="config/home.example.toml",
        help="chemin du fichier TOML",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    healthcheck = subparsers.add_parser(
        "healthcheck", help="contrôler la configuration HOME"
    )
    healthcheck.set_defaults(handler=_healthcheck)

    csv_parser = subparsers.add_parser(
        "validate-csv", help="valider entièrement un CSV sans l'importer"
    )
    csv_parser.add_argument("path")
    csv_parser.set_defaults(handler=_validate_csv)

    import_parser = subparsers.add_parser(
        'import-csv',
        help='valider puis importer un CSV dans MySQL',
    )
    import_parser.add_argument('path')
    import_parser.set_defaults(handler=_import_csv)

    scan_parser = subparsers.add_parser(
        "scan", help="inspecter le faux NAS et persister sauf --dry-run"
    )
    scan_parser.add_argument("--dry-run", action="store_true")
    scan_parser.add_argument(
        '--path',
        help='sous-dossier autorise du NAS HOME simule',
    )
    scan_parser.add_argument(
        '--test-hold-lock-seconds',
        type=float,
        default=0,
        help='maintenir le verrou scanner pour un test HOME concurrent',
    )
    scan_parser.set_defaults(handler=_scan)

    demo_parser = subparsers.add_parser(
        'demo-home',
        help='creer un Versand mock visualisable sans envoi reseau',
    )
    demo_parser.set_defaults(handler=_demo_home)

    manual_review_demo_parser = subparsers.add_parser(
        'demo-manual-review-home',
        help='demontrer les PDF inconnus et ambigus sans creer de Versand',
    )
    manual_review_demo_parser.set_defaults(
        handler=_demo_manual_review_home
    )

    prepare_mock_versand_parser = subparsers.add_parser(
        'prepare-mock-versand',
        help='preparer les Pruefprotokolle READY dans des .eml mock',
    )
    prepare_mock_versand_parser.set_defaults(
        handler=_prepare_mock_versand
    )
    prepare_mock_versand_parser.add_argument(
        '--at',
        help='instant ISO-8601 simule pour le cycle hebdomadaire',
    )
    prepare_mock_versand_parser.add_argument(
        '--simulate-timeout',
        action='store_true',
        help='simuler localement un timeout ambigu du fournisseur mock',
    )
    prepare_mock_versand_parser.add_argument(
        '--simulate-interruption-before-provider',
        action='store_true',
        help=(
            'interrompre HOME apres persistance PLANNED et avant le mock'
        ),
    )
    prepare_mock_versand_parser.add_argument(
        '--simulate-crash-after-provider',
        action='store_true',
        help=(
            'simuler un crash HOME apres le retour du mock et avant ACCEPTED'
        ),
    )
    prepare_mock_versand_parser.add_argument(
        '--test-hold-lock-seconds',
        type=float,
        default=0,
        help='maintenir le verrou Versand pour un test HOME concurrent',
    )

    manual_review_parser = subparsers.add_parser(
        'manual-review',
        help='lister ou traiter les Pruefprotokolle bloques',
    )
    manual_review_actions = manual_review_parser.add_subparsers(
        dest='manual_review_action',
        required=True,
    )
    manual_review_list_parser = manual_review_actions.add_parser(
        'list',
        help='afficher les Pruefprotokolle en attente',
    )
    manual_review_list_parser.set_defaults(handler=_manual_review_list)

    manual_review_assign_parser = manual_review_actions.add_parser(
        'assign',
        help='associer un Pruefprotokoll a un Auftrag actif',
    )
    manual_review_assign_parser.add_argument(
        'protokoll_id',
        type=int,
    )
    manual_review_assign_parser.add_argument('auftragsnummer')
    manual_review_assign_parser.add_argument(
        '--reason',
        required=True,
        help='raison tracee de l association',
    )
    manual_review_assign_parser.set_defaults(
        handler=_manual_review_assign
    )

    manual_review_ignore_parser = manual_review_actions.add_parser(
        'ignore',
        help='ignorer un Pruefprotokoll sans supprimer le fichier',
    )
    manual_review_ignore_parser.add_argument(
        'protokoll_id',
        type=int,
    )
    manual_review_ignore_parser.add_argument(
        '--reason',
        required=True,
        help='raison tracee de l exclusion',
    )
    manual_review_ignore_parser.set_defaults(
        handler=_manual_review_ignore
    )

    versand_review_parser = subparsers.add_parser(
        'versand-review',
        help='consulter separement les incidents Versand bloques',
    )
    versand_review_actions = versand_review_parser.add_subparsers(
        dest='versand_review_action',
        required=True,
    )
    versand_review_list_parser = versand_review_actions.add_parser(
        'list',
        help='lister les Versand MANUAL_REVIEW en lecture seule',
    )
    versand_review_list_parser.set_defaults(
        handler=_versand_review_list
    )
    versand_review_accept_parser = versand_review_actions.add_parser(
        'confirm-accepted',
        help='confirmer que le fournisseur a accepte le Versand',
    )
    versand_review_accept_parser.add_argument('versand_id', type=int)
    versand_review_accept_parser.add_argument('--reason', required=True)
    versand_review_accept_parser.set_defaults(
        handler=_versand_review_resolve,
        decision='PROVIDER_ACCEPTED',
    )
    versand_review_retry_parser = versand_review_actions.add_parser(
        'retry-not-accepted',
        help='effectuer le retry mock manuel unique apres non-acceptation',
    )
    versand_review_retry_parser.add_argument('versand_id', type=int)
    versand_review_retry_parser.add_argument('--reason', required=True)
    versand_review_retry_parser.set_defaults(
        handler=_versand_review_resolve,
        decision='PROVIDER_NOT_ACCEPTED_RETRY',
    )
    versand_review_keep_parser = versand_review_actions.add_parser(
        'keep-unknown',
        help='journaliser inconnu et conserver MANUAL_REVIEW',
    )
    versand_review_keep_parser.add_argument('versand_id', type=int)
    versand_review_keep_parser.add_argument('--reason', required=True)
    versand_review_keep_parser.set_defaults(
        handler=_versand_review_resolve,
        decision='KEEP_UNKNOWN',
    )

    manual_versand_parser = subparsers.add_parser(
        'manual-versand',
        help='enregistrer un Versand urgent effectue manuellement',
    )
    manual_versand_actions = manual_versand_parser.add_subparsers(
        dest='manual_versand_action',
        required=True,
    )
    manual_versand_record_parser = manual_versand_actions.add_parser(
        'record',
        help='declarer un Pruefprotokoll READY comme MANUALLY_SENT',
    )
    manual_versand_record_parser.add_argument('protokoll_id', type=int)
    manual_versand_record_parser.add_argument('--operator', required=True)
    manual_versand_record_parser.add_argument('--reason', required=True)
    manual_versand_record_parser.add_argument(
        '--at',
        help='date ISO-8601 avec decalage UTC explicite',
    )
    manual_versand_record_parser.set_defaults(
        handler=_manual_versand_record
    )

    pdf_parser = subparsers.add_parser(
        "inspect-pdf", help="contrôler un seul PDF"
    )
    pdf_parser.add_argument("path")
    pdf_parser.set_defaults(handler=_inspect_pdf)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.config)
        return int(args.handler(settings, args))
    except (
        ConfigError,
        CsvImportRejected,
        DatabaseUnavailable,
        FileNotFoundError,
        HomeDemoError,
        HomeDemoSafetyError,
        ManualReviewError,
        ManualReviewSafetyError,
        ManualVersandError,
        ManualVersandSafetyError,
        TimeoutError,
        VersandReviewSafetyError,
        VersandResolutionError,
    ) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2
