from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re


@dataclass(frozen=True, slots=True)
class PdfInspection:
    is_valid: bool
    reason: str
    auftragsnummer: str | None
    sha256: str | None


def extract_auftragsnummer(filename: str, pattern: str) -> str | None:
    match = re.fullmatch(pattern, filename, flags=re.IGNORECASE)
    if match is None:
        return None
    value = match.groupdict().get("auftragsnummer")
    return value.strip() if value and value.strip() else None


def calculate_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_pdf_container(path: Path) -> tuple[bool, str]:
    if not path.is_file():
        return False, "fichier introuvable"
    if path.suffix.lower() != ".pdf":
        return False, "extension différente de .pdf"
    if path.stat().st_size == 0:
        return False, "fichier vide"

    with path.open("rb") as stream:
        signature = stream.read(5)
        if signature != b"%PDF-":
            return False, "signature PDF absente"
        stream.seek(max(0, path.stat().st_size - 4096))
        tail = stream.read()
        if b"%%EOF" not in tail:
            return False, "marque de fin PDF absente"
    return True, "PDF valide"


def inspect_pdf(
    path: str | Path, filename_pattern: str
) -> PdfInspection:
    source = Path(path)
    valid, reason = _validate_pdf_container(source)
    sha256 = calculate_sha256(source) if source.is_file() else None
    if not valid:
        return PdfInspection(False, reason, None, sha256)

    auftragsnummer = extract_auftragsnummer(source.name, filename_pattern)
    return PdfInspection(
        is_valid=True,
        reason=(
            "PDF valide"
            if auftragsnummer
            else "Auftragsnummer absente ou nom non conforme"
        ),
        auftragsnummer=auftragsnummer,
        sha256=sha256,
    )
