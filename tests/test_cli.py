from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from pruefversand.cli import main
from pruefversand.config import load_settings
from pruefversand.database import (
    CsvImportResult,
    DatabaseUnavailable,
    MySqlProbeResult,
)
from pruefversand.models import ProtokollStatus
from pruefversand.mysql_scanner import ScanPersistenceResult
from pruefversand.scanner import FileObservation, ScanItem, ScanReport


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG = PROJECT_ROOT / 'config/home.example.toml'
CSV = PROJECT_ROOT / 'sample_data/baustellen_example.csv'
PDF = (
    PROJECT_ROOT
    / 'sample_data/nas_mock'
    / '00125083_F001_Pruefprotokoll_2026-07-25.pdf'
)


class CliDatabaseTests(unittest.TestCase):
    @patch('pruefversand.cli.probe_mysql')
    def test_healthcheck_requires_reachable_mysql(
        self,
        probe_mysql_mock: MagicMock,
    ) -> None:
        probe_mysql_mock.return_value = MySqlProbeResult('8.4.10')
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(['--config', str(CONFIG), 'healthcheck'])

        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload['status'], 'READY')
        self.assertEqual(payload['nas_status'], 'AVAILABLE')
        self.assertEqual(payload['mysql_status'], 'AVAILABLE')
        self.assertTrue(payload['database_checked'])
        self.assertEqual(payload['errors'], [])

    @patch('pruefversand.cli.probe_mysql')
    def test_healthcheck_reports_mysql_unavailable_without_traceback(
        self,
        probe_mysql_mock: MagicMock,
    ) -> None:
        probe_mysql_mock.side_effect = DatabaseUnavailable(
            'Connexion MySQL impossible'
        )
        output = StringIO()
        error = StringIO()

        with redirect_stdout(output), redirect_stderr(error):
            exit_code = main(['--config', str(CONFIG), 'healthcheck'])

        self.assertEqual(exit_code, 1)
        self.assertEqual(error.getvalue(), '')
        payload = json.loads(output.getvalue())
        self.assertEqual(payload['status'], 'NOT_READY')
        self.assertEqual(payload['mysql_status'], 'UNAVAILABLE')
        self.assertIn(
            'MYSQL_UNAVAILABLE',
            {item['code'] for item in payload['errors']},
        )

    @patch('pruefversand.cli.probe_mysql')
    @patch('pruefversand.cli.load_settings')
    def test_healthcheck_reports_missing_nas(
        self,
        load_settings_mock: MagicMock,
        probe_mysql_mock: MagicMock,
    ) -> None:
        settings = load_settings(CONFIG)
        load_settings_mock.return_value = replace(
            settings,
            app=replace(
                settings.app,
                nas_path=PROJECT_ROOT / 'sample_data/nas_missing_test',
            ),
        )
        probe_mysql_mock.return_value = MySqlProbeResult('8.4.10')
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(['--config', str(CONFIG), 'healthcheck'])

        self.assertEqual(exit_code, 1)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload['nas_status'], 'UNAVAILABLE')
        self.assertIn(
            'NAS_UNAVAILABLE',
            {item['code'] for item in payload['errors']},
        )

    @patch('pruefversand.cli.import_baustellen_csv')
    @patch('pruefversand.cli.connect_mysql')
    def test_import_csv_uses_transactional_adapter(
        self,
        connect_mysql_mock: MagicMock,
        import_mock: MagicMock,
    ) -> None:
        import_mock.return_value = CsvImportResult(
            csv_sha256='a' * 64,
            total_rows=3,
            inserted_auftraege=2,
            updated_auftraege=0,
            duplicate_import=False,
        )
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(
                [
                    '--config',
                    str(CONFIG),
                    'import-csv',
                    str(CSV),
                ]
            )

        self.assertEqual(exit_code, 0)
        connect_mysql_mock.assert_called_once()
        import_mock.assert_called_once_with(
            connect_mysql_mock.return_value,
            str(CSV),
        )
        connect_mysql_mock.return_value.close.assert_called_once()
        payload = json.loads(output.getvalue())
        self.assertEqual(payload['status'], 'IMPORTED')
        self.assertEqual(payload['inserted_auftraege'], 2)
        self.assertEqual(payload['deleted_auftraege'], 0)
        self.assertEqual(payload['deactivated_auftraege'], 0)

    @patch('pruefversand.cli.scan_and_persist')
    @patch('pruefversand.cli.connect_mysql')
    def test_scan_without_dry_run_persists_in_mysql(
        self,
        connect_mysql_mock: MagicMock,
        scan_mock: MagicMock,
    ) -> None:
        observation = FileObservation(628, 123, 'b' * 64)
        report = ScanReport(
            items=(
                ScanItem(
                    path=PDF,
                    status=ProtokollStatus.DISCOVERED,
                    reason='premiere observation',
                    auftragsnummer='00125083',
                    observation=observation,
                ),
            ),
            observations={str(PDF.resolve()): observation},
        )
        scan_mock.return_value = ScanPersistenceResult(report, 1, 0)
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(['--config', str(CONFIG), 'scan'])

        self.assertEqual(exit_code, 0)
        scan_mock.assert_called_once()
        connect_mysql_mock.return_value.close.assert_called_once()
        payload = json.loads(output.getvalue())
        self.assertEqual(payload['mode'], 'mysql')
        self.assertTrue(payload['dry_run'])
        self.assertEqual(payload['mail_provider'], 'mock')
        self.assertEqual(payload['inserted_protokolle'], 1)

    @patch('pruefversand.cli.connect_mysql')
    def test_scan_dry_run_never_opens_mysql(
        self,
        connect_mysql_mock: MagicMock,
    ) -> None:
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(
                ['--config', str(CONFIG), 'scan', '--dry-run']
            )

        self.assertEqual(exit_code, 0)
        connect_mysql_mock.assert_not_called()
        payload = json.loads(output.getvalue())
        self.assertEqual(payload['mode'], 'dry-run')
        self.assertEqual(
            payload['status'],
            'DRY_RUN_COMPLETE_NO_PERSISTENCE',
        )
        self.assertFalse(payload['database_checked'])
        self.assertFalse(payload['persistence_performed'])

    @patch('pruefversand.cli.connect_mysql')
    @patch('pruefversand.cli.load_settings')
    def test_scan_dry_run_reports_missing_nas_without_mysql(
        self,
        load_settings_mock: MagicMock,
        connect_mysql_mock: MagicMock,
    ) -> None:
        settings = load_settings(CONFIG)
        load_settings_mock.return_value = replace(
            settings,
            app=replace(
                settings.app,
                nas_path=PROJECT_ROOT / 'sample_data/nas_missing_scan',
            ),
        )
        error = StringIO()

        with redirect_stderr(error):
            exit_code = main(
                ['--config', str(CONFIG), 'scan', '--dry-run']
            )

        self.assertEqual(exit_code, 2)
        self.assertIn('Dossier NAS introuvable', error.getvalue())
        self.assertNotIn('Traceback', error.getvalue())
        connect_mysql_mock.assert_not_called()

    @patch('pruefversand.cli.connect_mysql')
    def test_scan_refuses_path_outside_home_nas(
        self,
        connect_mysql_mock: MagicMock,
    ) -> None:
        exit_code = main(
            [
                '--config',
                str(CONFIG),
                'scan',
                '--dry-run',
                '--path',
                str(PROJECT_ROOT.parent),
            ]
        )

        self.assertEqual(exit_code, 2)
        connect_mysql_mock.assert_not_called()

    @patch('pruefversand.cli.connect_mysql')
    def test_weekly_versand_refuses_invalid_simulated_date(
        self,
        connect_mysql_mock: MagicMock,
    ) -> None:
        exit_code = main(
            [
                '--config',
                str(CONFIG),
                'prepare-mock-versand',
                '--at',
                'date-invalide',
            ]
        )

        self.assertEqual(exit_code, 2)
        connect_mysql_mock.assert_not_called()

    @patch('pruefversand.cli.connect_mysql')
    def test_weekly_versand_refuses_simulated_date_without_utc_offset(
        self,
        connect_mysql_mock: MagicMock,
    ) -> None:
        exit_code = main(
            [
                '--config',
                str(CONFIG),
                'prepare-mock-versand',
                '--at',
                '2026-07-31T16:00:00',
            ]
        )

        self.assertEqual(exit_code, 2)
        connect_mysql_mock.assert_not_called()

    @patch('pruefversand.cli.prepare_ready_mock_versand')
    @patch('pruefversand.cli.connect_mysql')
    def test_prepare_mock_versand_exposes_timeout_simulation(
        self,
        connect_mysql_mock: MagicMock,
        prepare_mock: MagicMock,
    ) -> None:
        result = MagicMock()
        result.weekly_due = None
        result.period_start = None
        result.ready_document_count = 0
        result.already_processed_count = 0
        result.messages = ()
        result.reused_message_paths = ()
        result.skipped_auftraege = ()
        result.manual_review_incidents = ()
        result.resumed_versand_count = 0
        result.provider_call_count = 0
        result.lock_acquired = True
        result.skipped_due_to_lock = False
        prepare_mock.return_value = result
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(
                [
                    '--config',
                    str(CONFIG),
                    'prepare-mock-versand',
                    '--simulate-timeout',
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertTrue(
            prepare_mock.call_args.kwargs['simulate_timeout']
        )
        connect_mysql_mock.return_value.close.assert_called_once()
        payload = json.loads(output.getvalue())
        self.assertEqual(payload['manual_review_incident_count'], 0)

    @patch('pruefversand.cli.prepare_ready_mock_versand')
    @patch('pruefversand.cli.connect_mysql')
    def test_prepare_mock_versand_exposes_controlled_interruption(
        self,
        connect_mysql_mock: MagicMock,
        prepare_mock: MagicMock,
    ) -> None:
        result = MagicMock()
        result.weekly_due = None
        result.period_start = None
        result.ready_document_count = 0
        result.already_processed_count = 0
        result.messages = ()
        result.reused_message_paths = ()
        result.skipped_auftraege = ()
        result.manual_review_incidents = ()
        result.resumed_versand_count = 0
        result.provider_call_count = 0
        result.lock_acquired = True
        result.skipped_due_to_lock = False
        prepare_mock.return_value = result

        exit_code = main(
            [
                '--config',
                str(CONFIG),
                'prepare-mock-versand',
                '--simulate-interruption-before-provider',
            ]
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue(
            prepare_mock.call_args.kwargs[
                'simulate_interruption_before_provider'
            ]
        )
        connect_mysql_mock.return_value.close.assert_called_once()

    @patch('pruefversand.cli.prepare_ready_mock_versand')
    @patch('pruefversand.cli.connect_mysql')
    def test_prepare_mock_versand_exposes_crash_after_provider(
        self,
        connect_mysql_mock: MagicMock,
        prepare_mock: MagicMock,
    ) -> None:
        result = MagicMock()
        result.weekly_due = None
        result.period_start = None
        result.ready_document_count = 0
        result.already_processed_count = 0
        result.messages = ()
        result.reused_message_paths = ()
        result.skipped_auftraege = ()
        result.manual_review_incidents = ()
        result.resumed_versand_count = 0
        result.provider_call_count = 0
        result.lock_acquired = True
        result.skipped_due_to_lock = False
        prepare_mock.return_value = result

        exit_code = main(
            [
                '--config',
                str(CONFIG),
                'prepare-mock-versand',
                '--simulate-crash-after-provider',
            ]
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue(
            prepare_mock.call_args.kwargs[
                'simulate_crash_after_provider'
            ]
        )
        connect_mysql_mock.return_value.close.assert_called_once()

    @patch('pruefversand.cli.scan_and_persist')
    @patch('pruefversand.cli.connect_mysql')
    def test_scan_lock_contention_exits_cleanly(
        self,
        connect_mysql_mock: MagicMock,
        scan_mock: MagicMock,
    ) -> None:
        scan_mock.return_value = ScanPersistenceResult(
            report=ScanReport(items=(), observations={}),
            inserted_protokolle=0,
            updated_protokolle=0,
            lock_acquired=False,
            skipped_due_to_lock=True,
        )
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(
                ['--config', str(CONFIG), 'scan']
            )

        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload['status'], 'SKIPPED_LOCK_BUSY')
        self.assertFalse(payload['lock_acquired'])
        self.assertEqual(payload['files'], [])

    @patch('pruefversand.cli.prepare_ready_mock_versand')
    @patch('pruefversand.cli.connect_mysql')
    def test_versand_lock_contention_exits_cleanly(
        self,
        connect_mysql_mock: MagicMock,
        prepare_mock: MagicMock,
    ) -> None:
        result = MagicMock()
        result.weekly_due = None
        result.period_start = None
        result.ready_document_count = 0
        result.already_processed_count = 0
        result.messages = ()
        result.reused_message_paths = ()
        result.skipped_auftraege = ()
        result.manual_review_incidents = ()
        result.resumed_versand_count = 0
        result.provider_call_count = 0
        result.lock_acquired = False
        result.skipped_due_to_lock = True
        prepare_mock.return_value = result
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(
                [
                    '--config',
                    str(CONFIG),
                    'prepare-mock-versand',
                ]
            )

        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload['status'], 'SKIPPED_LOCK_BUSY')
        self.assertEqual(payload['new_message_count'], 0)
        self.assertEqual(payload['provider_call_count'], 0)


if __name__ == '__main__':
    unittest.main()
