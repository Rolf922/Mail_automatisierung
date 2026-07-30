from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from pruefversand.cli import main
from pruefversand.config import load_settings
from pruefversand.manual_versand import (
    ManualVersandSafetyError,
    assert_manual_versand_safety,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG = PROJECT_ROOT / 'config/home.example.toml'


class ManualVersandSafetyTests(unittest.TestCase):
    def test_refuses_mysql80_port(self) -> None:
        settings = load_settings(CONFIG)
        unsafe = replace(
            settings,
            database=replace(settings.database, port=3306),
        )

        with self.assertRaisesRegex(ManualVersandSafetyError, '3307'):
            assert_manual_versand_safety(unsafe)


class ManualVersandCliTests(unittest.TestCase):
    @patch('pruefversand.cli.connect_mysql')
    def test_cancelled_before_mysql_connection(
        self,
        connect_mysql_mock: MagicMock,
    ) -> None:
        output = StringIO()
        with patch('builtins.input', return_value='NON'):
            with redirect_stdout(output):
                exit_code = main(
                    [
                        '--config',
                        str(CONFIG),
                        'manual-versand',
                        'record',
                        '7',
                        '--operator',
                        'Utilisateur HOME',
                        '--reason',
                        'Versand urgent synthétique',
                    ]
                )

        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload['status'], 'CANCELLED')
        self.assertFalse(payload['modified'])
        connect_mysql_mock.assert_not_called()

    @patch('pruefversand.cli.connect_mysql')
    def test_naive_date_is_rejected_before_mysql_connection(
        self,
        connect_mysql_mock: MagicMock,
    ) -> None:
        exit_code = main(
            [
                '--config',
                str(CONFIG),
                'manual-versand',
                'record',
                '7',
                '--operator',
                'Utilisateur HOME',
                '--reason',
                'Versand urgent synthétique',
                '--at',
                '2026-08-03T10:00:00',
            ]
        )

        self.assertEqual(exit_code, 2)
        connect_mysql_mock.assert_not_called()


if __name__ == '__main__':
    unittest.main()
