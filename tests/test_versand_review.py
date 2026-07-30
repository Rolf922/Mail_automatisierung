from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime
from io import StringIO
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from pruefversand.cli import main
from pruefversand.config import load_settings
from pruefversand.versand_review import (
    VersandReviewDocument,
    VersandReviewIncident,
    VersandReviewSafetyError,
    assert_versand_review_safety,
    list_versand_review_incidents,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG = PROJECT_ROOT / 'config/home.example.toml'
SCRIPT = PROJECT_ROOT / 'scripts/manual_review_home.ps1'


class VersandReviewSafetyTests(unittest.TestCase):
    def test_refuses_mysql80_port(self) -> None:
        settings = load_settings(CONFIG)
        unsafe = replace(
            settings,
            database=replace(settings.database, port=3306),
        )

        with self.assertRaisesRegex(VersandReviewSafetyError, '3307'):
            assert_versand_review_safety(unsafe)

    def test_list_uses_read_only_transaction_without_commit(self) -> None:
        settings = load_settings(CONFIG)
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchall.return_value = [
            (
                5,
                '00125083',
                'timeout ambigu simule',
                datetime(2026, 7, 27, 15, 18, 35),
                5,
                '00125083_TIMEOUT_AMBIGU_20260727.pdf',
            )
        ]

        incidents = list_versand_review_incidents(connection, settings)

        self.assertEqual(incidents[0].versand_id, 5)
        self.assertTrue(incidents[0].automatic_retry_blocked)
        connection.start_transaction.assert_called_once_with(readonly=True)
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()

    def test_powershell_list_contains_no_demo_import_or_scan(self) -> None:
        script = SCRIPT.read_text(encoding='utf-8')

        self.assertNotIn('demo-manual-review-home', script)
        self.assertNotIn('docker run', script)
        self.assertNotIn('import-csv', script)
        self.assertNotIn(' scan --', script)
        self.assertIn("'VersandList'", script)
        self.assertIn("'VersandAccept'", script)
        self.assertIn("'VersandRetry'", script)
        self.assertIn("'VersandKeep'", script)


class VersandReviewCliTests(unittest.TestCase):
    @patch('pruefversand.cli.list_versand_review_incidents')
    @patch('pruefversand.cli.connect_mysql')
    def test_cli_displays_incident_fields_separately(
        self,
        connect_mysql_mock: MagicMock,
        list_mock: MagicMock,
    ) -> None:
        list_mock.return_value = (
            VersandReviewIncident(
                versand_id=5,
                auftragsnummer='00125083',
                reason='timeout ambigu simule',
                incident_at=datetime(2026, 7, 27, 15, 18, 35),
                linked_documents=(
                    VersandReviewDocument(
                        protokoll_id=5,
                        filename=(
                            '00125083_TIMEOUT_AMBIGU_20260727.pdf'
                        ),
                    ),
                ),
            ),
        )
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(
                [
                    '--config',
                    str(CONFIG),
                    'versand-review',
                    'list',
                ]
            )

        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload['review_type'], 'VERSAND')
        self.assertTrue(payload['read_only'])
        self.assertEqual(payload['items'][0]['versand_id'], 5)
        self.assertEqual(payload['items'][0]['auftragsnummer'], '00125083')
        self.assertEqual(
            payload['items'][0]['linked_documents'][0]['protokoll_id'],
            5,
        )
        self.assertTrue(
            payload['items'][0]['automatic_retry_blocked']
        )
        connect_mysql_mock.return_value.close.assert_called_once()

    @patch('pruefversand.cli.connect_mysql')
    def test_all_resolution_actions_cancel_before_mysql_connection(
        self,
        connect_mysql_mock: MagicMock,
    ) -> None:
        actions = (
            'confirm-accepted',
            'retry-not-accepted',
            'keep-unknown',
        )
        for action in actions:
            with self.subTest(action=action), patch(
                'builtins.input',
                return_value='NON',
            ):
                output = StringIO()
                with redirect_stdout(output):
                    exit_code = main(
                        [
                            '--config',
                            str(CONFIG),
                            'versand-review',
                            action,
                            '3',
                            '--reason',
                            'Decision HOME synthetique',
                        ]
                    )
                self.assertEqual(exit_code, 0)
                payload = json.loads(output.getvalue())
                self.assertEqual(payload['status'], 'CANCELLED')
                self.assertFalse(payload['modified'])
        connect_mysql_mock.assert_not_called()


if __name__ == '__main__':
    unittest.main()
