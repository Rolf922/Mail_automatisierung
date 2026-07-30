from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class ProtokollStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    READY = "READY"
    SENDING = "SENDING"
    ACCEPTED = "ACCEPTED"
    FAILED = "FAILED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    QUARANTINED = "QUARANTINED"
    MANUALLY_SENT = "MANUALLY_SENT"


@dataclass(frozen=True, slots=True)
class Recipient:
    email: str
    kind: str
    name: str = ""


@dataclass(frozen=True, slots=True)
class ReadyDocument:
    path: Path
    auftragsnummer: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class MailBatch:
    auftragsnummer: str
    documents: tuple[ReadyDocument, ...]
    recipients: tuple[Recipient, ...]

