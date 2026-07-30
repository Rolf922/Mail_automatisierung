from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from pruefversand.models import ProtokollStatus
from pruefversand.pdf_tools import inspect_pdf


@dataclass(frozen=True, slots=True)
class FileObservation:
    size_bytes: int
    mtime_ns: int
    sha256: str | None


@dataclass(frozen=True, slots=True)
class ScanItem:
    path: Path
    status: ProtokollStatus
    reason: str
    auftragsnummer: str | None
    observation: FileObservation


@dataclass(frozen=True, slots=True)
class ScanReport:
    items: tuple[ScanItem, ...]
    observations: dict[str, FileObservation]


def _same_observation(
    previous: FileObservation | None, current: FileObservation
) -> bool:
    return previous == current


def scan_directory(
    nas_path: str | Path,
    filename_pattern: str,
    previous_observations: Mapping[str, FileObservation] | None = None,
) -> ScanReport:
    root = Path(nas_path)
    if not root.is_dir():
        raise FileNotFoundError(f"Dossier NAS introuvable : {root}")

    previous = previous_observations or {}
    items: list[ScanItem] = []
    current_observations: dict[str, FileObservation] = {}

    candidates = sorted(
        (
            entry
            for entry in root.iterdir()
            if entry.is_file() and entry.suffix.lower() == ".pdf"
        ),
        key=lambda entry: entry.name.casefold(),
    )

    for path in candidates:
        stat_before = path.stat()
        inspection = inspect_pdf(path, filename_pattern)
        stat_after = path.stat()
        changed_during_read = (
            stat_before.st_size != stat_after.st_size
            or stat_before.st_mtime_ns != stat_after.st_mtime_ns
        )
        observation = FileObservation(
            size_bytes=stat_after.st_size,
            mtime_ns=stat_after.st_mtime_ns,
            sha256=inspection.sha256,
        )
        key = str(path.resolve())
        current_observations[key] = observation

        stable = (
            not changed_during_read
            and _same_observation(previous.get(key), observation)
        )
        if not stable:
            status = ProtokollStatus.DISCOVERED
            reason = "première observation ou fichier modifié"
        elif not inspection.is_valid:
            status = ProtokollStatus.QUARANTINED
            reason = inspection.reason
        elif inspection.auftragsnummer is None:
            status = ProtokollStatus.MANUAL_REVIEW
            reason = inspection.reason
        else:
            status = ProtokollStatus.READY
            reason = "PDF stable, valide et nom conforme"

        items.append(
            ScanItem(
                path=path,
                status=status,
                reason=reason,
                auftragsnummer=inspection.auftragsnummer,
                observation=observation,
            )
        )

    return ScanReport(
        items=tuple(items),
        observations=current_observations,
    )

