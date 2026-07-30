from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import MagicMock

from pruefversand.config import load_settings
from pruefversand.home_demo import HomeDemoSafetyError, run_home_demo


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG = PROJECT_ROOT / 'config/home.example.toml'


class HomeDemoSafetyTests(unittest.TestCase):
    def test_refuses_real_mail_provider(self) -> None:
        settings = load_settings(CONFIG)
        unsafe = replace(
            settings,
            mail=replace(settings.mail, provider='smtp'),
        )
        connection = MagicMock()

        with self.assertRaisesRegex(HomeDemoSafetyError, 'provider'):
            run_home_demo(connection, unsafe)

        connection.cursor.assert_not_called()

    def test_refuses_wrong_home_mysql_port(self) -> None:
        settings = load_settings(CONFIG)
        unsafe = replace(
            settings,
            database=replace(settings.database, port=3306),
        )
        connection = MagicMock()

        with self.assertRaisesRegex(HomeDemoSafetyError, '3307'):
            run_home_demo(connection, unsafe)

        connection.cursor.assert_not_called()


if __name__ == '__main__':
    unittest.main()
