from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
import re


REQUIRED_COLUMNS = frozenset(
    {
        "auftragsnummer",
        "baustelle_name",
        "kunde_name",
        "kontakt_name",
        "email",
        "empfaengertyp",
        "aktiv",
    }
)
EMAIL_PATTERN = re.compile(
    r"^[^@\s]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$"
)


@dataclass(frozen=True, slots=True)
class CsvIssue:
    line_number: int
    field: str
    message: str


@dataclass(frozen=True, slots=True)
class BaustelleRecord:
    auftragsnummer: str
    baustelle_name: str
    kunde_name: str
    kontakt_name: str
    email: str
    empfaengertyp: str
    aktiv: bool


@dataclass(frozen=True, slots=True)
class CsvValidationResult:
    records: tuple[BaustelleRecord, ...]
    issues: tuple[CsvIssue, ...]
    total_rows: int

    @property
    def is_valid(self) -> bool:
        return not self.issues


def _clean_row(row: dict[str, str | None]) -> dict[str, str]:
    return {
        str(key).strip(): (value or "").strip()
        for key, value in row.items()
        if key is not None
    }


def _validate_row(
    row: dict[str, str], line_number: int
) -> tuple[BaustelleRecord | None, list[CsvIssue]]:
    issues: list[CsvIssue] = []

    for field in (
        "auftragsnummer",
        "baustelle_name",
        "kunde_name",
        "email",
        "empfaengertyp",
        "aktiv",
    ):
        if not row.get(field):
            issues.append(CsvIssue(line_number, field, "champ obligatoire"))

    auftragsnummer = row.get("auftragsnummer", "")
    if auftragsnummer and (
        len(auftragsnummer) > 64
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", auftragsnummer)
    ):
        issues.append(
            CsvIssue(
                line_number,
                "auftragsnummer",
                "utiliser 1 à 64 lettres, chiffres, points, '_' ou '-'",
            )
        )

    email = row.get("email", "").lower()
    if email and not EMAIL_PATTERN.fullmatch(email):
        issues.append(CsvIssue(line_number, "email", "adresse e-mail invalide"))

    recipient_kind = row.get("empfaengertyp", "").upper()
    if recipient_kind and recipient_kind not in {"TO", "CC"}:
        issues.append(
            CsvIssue(line_number, "empfaengertyp", "valeur attendue : TO ou CC")
        )

    active_raw = row.get("aktiv", "")
    if active_raw not in {"0", "1"}:
        issues.append(CsvIssue(line_number, "aktiv", "valeur attendue : 0 ou 1"))

    if issues:
        return None, issues

    return (
        BaustelleRecord(
            auftragsnummer=auftragsnummer,
            baustelle_name=row["baustelle_name"],
            kunde_name=row["kunde_name"],
            kontakt_name=row.get("kontakt_name", ""),
            email=email,
            empfaengertyp=recipient_kind,
            aktiv=active_raw == "1",
        ),
        issues,
    )


def validate_csv(path: str | Path) -> CsvValidationResult:
    source = Path(path)
    if not source.is_file():
        return CsvValidationResult(
            records=(),
            issues=(CsvIssue(0, "file", f"fichier introuvable : {source}"),),
            total_rows=0,
        )

    records: list[BaustelleRecord] = []
    issues: list[CsvIssue] = []
    seen_recipients: set[tuple[str, str, str]] = set()
    total_rows = 0

    try:
        with source.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            headers = {header.strip() for header in (reader.fieldnames or [])}
            missing = sorted(REQUIRED_COLUMNS - headers)
            if missing:
                return CsvValidationResult(
                    records=(),
                    issues=(
                        CsvIssue(
                            1,
                            "header",
                            "colonnes manquantes : " + ", ".join(missing),
                        ),
                    ),
                    total_rows=0,
                )

            for line_number, raw_row in enumerate(reader, start=2):
                total_rows += 1
                row = _clean_row(raw_row)
                record, row_issues = _validate_row(row, line_number)
                issues.extend(row_issues)
                if record is None:
                    continue
                recipient_key = (
                    record.auftragsnummer,
                    record.email,
                    record.empfaengertyp,
                )
                if recipient_key in seen_recipients:
                    issues.append(
                        CsvIssue(
                            line_number,
                            "email",
                            "destinataire dupliqué pour cet Auftrag",
                        )
                    )
                    continue
                seen_recipients.add(recipient_key)
                records.append(record)
    except UnicodeDecodeError as exc:
        issues.append(CsvIssue(0, "encoding", f"CSV non UTF-8 : {exc}"))
    except csv.Error as exc:
        issues.append(CsvIssue(0, "csv", f"CSV illisible : {exc}"))

    return CsvValidationResult(
        records=tuple(records),
        issues=tuple(issues),
        total_rows=total_rows,
    )

