from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from pathlib import Path
import re
import tomllib


class ConfigError(ValueError):
    """Raised when a configuration could make the workflow unsafe."""


@dataclass(frozen=True, slots=True)
class AppSettings:
    environment: str
    dry_run: bool
    timezone: str
    scan_interval_seconds: int
    nas_path: Path
    quarantine_path: Path
    filename_pattern: str
    maximum_attachment_bytes: int


@dataclass(frozen=True, slots=True)
class ScheduleSettings:
    weekday: int
    time_of_day: time


@dataclass(frozen=True, slots=True)
class MailSettings:
    provider: str
    mock_outbox_path: Path
    redirect_to: str
    allowed_recipient_domains: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    host: str
    port: int
    name: str
    user: str
    password_env: str
    require_tls: bool


@dataclass(frozen=True, slots=True)
class Settings:
    app: AppSettings
    schedule: ScheduleSettings
    mail: MailSettings
    database: DatabaseSettings
    source_path: Path
    project_root: Path


def _required(mapping: dict[str, object], key: str, section: str) -> object:
    if key not in mapping:
        raise ConfigError(f"Clé obligatoire manquante : [{section}] {key}")
    return mapping[key]


def _project_root(config_path: Path) -> Path:
    parent = config_path.resolve().parent
    return parent.parent if parent.name == "config" else parent


def _resolve_path(raw: object, root: Path, section: str, key: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError(f"[{section}] {key} doit être un chemin non vide")
    path = Path(raw)
    return path if path.is_absolute() else root / path


def _parse_time(raw: object) -> time:
    if not isinstance(raw, str):
        raise ConfigError("[schedule] time doit avoir le format HH:MM")
    try:
        hour, minute = (int(value) for value in raw.split(":", maxsplit=1))
        return time(hour=hour, minute=minute)
    except (TypeError, ValueError) as exc:
        raise ConfigError("[schedule] time doit avoir le format HH:MM") from exc


def load_settings(path: str | Path) -> Settings:
    source = Path(path).resolve()
    if not source.is_file():
        raise ConfigError(f"Fichier de configuration introuvable : {source}")

    with source.open("rb") as stream:
        raw = tomllib.load(stream)

    root = _project_root(source)
    app_raw = raw.get("app")
    schedule_raw = raw.get("schedule")
    mail_raw = raw.get("mail")
    database_raw = raw.get("database")
    for name, section in (
        ("app", app_raw),
        ("schedule", schedule_raw),
        ("mail", mail_raw),
        ("database", database_raw),
    ):
        if not isinstance(section, dict):
            raise ConfigError(f"Section [{name}] manquante ou invalide")

    environment = str(_required(app_raw, "environment", "app")).lower()
    dry_run = _required(app_raw, "dry_run", "app")
    if not isinstance(dry_run, bool):
        raise ConfigError("[app] dry_run doit être true ou false")

    filename_pattern = str(
        _required(app_raw, "filename_pattern", "app")
    )
    try:
        compiled_pattern = re.compile(filename_pattern, re.IGNORECASE)
    except re.error as exc:
        raise ConfigError(f"[app] filename_pattern invalide : {exc}") from exc
    if "auftragsnummer" not in compiled_pattern.groupindex:
        raise ConfigError(
            "[app] filename_pattern doit contenir le groupe "
            "(?P<auftragsnummer>...)"
        )

    scan_interval = int(
        _required(app_raw, "scan_interval_seconds", "app")
    )
    maximum_attachment_bytes = int(
        _required(app_raw, "maximum_attachment_bytes", "app")
    )
    if scan_interval < 60:
        raise ConfigError("[app] scan_interval_seconds doit être au moins 60")
    if maximum_attachment_bytes <= 0:
        raise ConfigError("[app] maximum_attachment_bytes doit être positif")

    weekday = int(_required(schedule_raw, "weekday", "schedule"))
    if weekday not in range(7):
        raise ConfigError("[schedule] weekday doit être compris entre 0 et 6")

    provider = str(_required(mail_raw, "provider", "mail")).lower()
    allowed_domains_raw = _required(
        mail_raw, "allowed_recipient_domains", "mail"
    )
    if not isinstance(allowed_domains_raw, list) or not allowed_domains_raw:
        raise ConfigError(
            "[mail] allowed_recipient_domains doit être une liste non vide"
        )
    allowed_domains = tuple(
        str(domain).strip().lower() for domain in allowed_domains_raw
    )
    if any(not domain or "@" in domain for domain in allowed_domains):
        raise ConfigError("[mail] domaine destinataire invalide")

    if environment not in {"home", "test", "company"}:
        raise ConfigError("[app] environment doit être home, test ou company")
    if environment in {"home", "test"}:
        if not dry_run:
            raise ConfigError("HOME/TEST exige dry_run = true")
        if provider != "mock":
            raise ConfigError("HOME/TEST exige mail.provider = 'mock'")
        if any(domain != "example.invalid" for domain in allowed_domains):
            raise ConfigError(
                "HOME/TEST autorise uniquement le domaine example.invalid"
            )
    if provider not in {"mock", "graph", "smtp"}:
        raise ConfigError("[mail] provider doit être mock, graph ou smtp")

    app = AppSettings(
        environment=environment,
        dry_run=dry_run,
        timezone=str(_required(app_raw, "timezone", "app")),
        scan_interval_seconds=scan_interval,
        nas_path=_resolve_path(
            _required(app_raw, "nas_path", "app"), root, "app", "nas_path"
        ),
        quarantine_path=_resolve_path(
            _required(app_raw, "quarantine_path", "app"),
            root,
            "app",
            "quarantine_path",
        ),
        filename_pattern=filename_pattern,
        maximum_attachment_bytes=maximum_attachment_bytes,
    )
    schedule = ScheduleSettings(
        weekday=weekday,
        time_of_day=_parse_time(_required(schedule_raw, "time", "schedule")),
    )
    mail = MailSettings(
        provider=provider,
        mock_outbox_path=_resolve_path(
            _required(mail_raw, "mock_outbox_path", "mail"),
            root,
            "mail",
            "mock_outbox_path",
        ),
        redirect_to=str(mail_raw.get("redirect_to", "")).strip(),
        allowed_recipient_domains=allowed_domains,
    )
    database = DatabaseSettings(
        host=str(_required(database_raw, "host", "database")),
        port=int(_required(database_raw, "port", "database")),
        name=str(_required(database_raw, "name", "database")),
        user=str(_required(database_raw, "user", "database")),
        password_env=str(
            _required(database_raw, "password_env", "database")
        ),
        require_tls=bool(
            _required(database_raw, "require_tls", "database")
        ),
    )
    if not 1 <= database.port <= 65535:
        raise ConfigError("[database] port invalide")

    return Settings(
        app=app,
        schedule=schedule,
        mail=mail,
        database=database,
        source_path=source,
        project_root=root,
    )

