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
from pruefversand.manual_review import (
    ManualReviewSafetyError,
    assert_manual_review_safety,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG = PROJECT_ROOT / 'config/home.example.toml'


class ManualReviewSafetyTests(unittest.TestCase):
    def test_refuses_real_mail_provider(self) -> None:
        settings = load_settings(CONFIG)
        unsafe = replace(
            settings,
            mail=replace(settings.mail, provider='smtp'),
        )

        with self.assertRaisesRegex(ManualReviewSafetyError, 'mock'):
            assert_manual_review_safety(unsafe)

    def test_refuses_mysql80_port(self) -> None:
        settings = load_settings(CONFIG)
        unsafe = replace(
            settings,
            database=replace(settings.database, port=3306),
        )

        with self.assertRaisesRegex(ManualReviewSafetyError, '3307'):
            assert_manual_review_safety(unsafe)


class ManualReviewConfirmationTests(unittest.TestCase):
    @patch('pruefversand.cli.connect_mysql')
    @patch('builtins.input', return_value='NON')
    def test_assign_cancelled_before_mysql_connection(
        self,
        input_mock: MagicMock,
        connect_mysql_mock: MagicMock,
    ) -> None:
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(
                [
                    '--config',
                    str(CONFIG),
                    'manual-review',
                    'assign',
                    '7',
                    '00125083',
                    '--reason',
                    'Test synthetique',
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output.getvalue())['status'], 'CANCELLED')
        input_mock.assert_called_once()
        connect_mysql_mock.assert_not_called()

    @patch('pruefversand.cli.connect_mysql')
    @patch('builtins.input', return_value='')
    def test_ignore_cancelled_before_mysql_connection(
        self,
        input_mock: MagicMock,
        connect_mysql_mock: MagicMock,
    ) -> None:
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(
                [
                    '--config',
                    str(CONFIG),
                    'manual-review',
                    'ignore',
                    '8',
                    '--reason',
                    'Test synthetique',
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output.getvalue())['status'], 'CANCELLED')
        input_mock.assert_called_once()
        connect_mysql_mock.assert_not_called()


if __name__ == '__main__':
    unittest.main()
