from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from pruefversand.config import ConfigError, load_settings


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ConfigTests(unittest.TestCase):
    def test_home_example_is_safe(self) -> None:
        settings = load_settings(PROJECT_ROOT / "config/home.example.toml")

        self.assertEqual(settings.app.environment, "home")
        self.assertTrue(settings.app.dry_run)
        self.assertEqual(settings.mail.provider, "mock")
        self.assertEqual(settings.database.port, 3307)
        self.assertEqual(
            settings.mail.allowed_recipient_domains,
            ("example.invalid",),
        )

    def test_home_refuses_real_sending(self) -> None:
        source = (
            PROJECT_ROOT / "config/home.example.toml"
        ).read_text(encoding="utf-8")
        unsafe = source.replace("dry_run = true", "dry_run = false", 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unsafe.toml"
            path.write_text(unsafe, encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "dry_run"):
                load_settings(path)

    def test_home_refuses_real_provider(self) -> None:
        source = (
            PROJECT_ROOT / "config/home.example.toml"
        ).read_text(encoding="utf-8")
        unsafe = source.replace('provider = "mock"', 'provider = "smtp"', 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unsafe.toml"
            path.write_text(unsafe, encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "provider"):
                load_settings(path)

    def test_compose_uses_safe_configurable_home_port(self) -> None:
        compose = (PROJECT_ROOT / 'compose.home.yml').read_text(
            encoding='utf-8'
        )

        self.assertIn(
            '127.0.0.1:${PRUEFVERSAND_MYSQL_PORT:-3307}:3306',
            compose,
        )


if __name__ == "__main__":
    unittest.main()
