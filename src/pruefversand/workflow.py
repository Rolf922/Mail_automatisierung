from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping

from pruefversand.models import MailBatch, ReadyDocument, Recipient


@dataclass(frozen=True, slots=True)
class BlockedBatch:
    auftragsnummer: str
    reason: str
    documents: tuple[ReadyDocument, ...]


@dataclass(frozen=True, slots=True)
class BatchBuildResult:
    batches: tuple[MailBatch, ...]
    blocked: tuple[BlockedBatch, ...]


def build_batches(
    documents: Iterable[ReadyDocument],
    recipients_by_auftrag: Mapping[str, Iterable[Recipient]],
) -> BatchBuildResult:
    grouped: dict[str, list[ReadyDocument]] = defaultdict(list)
    for document in documents:
        grouped[document.auftragsnummer].append(document)

    batches: list[MailBatch] = []
    blocked: list[BlockedBatch] = []
    for auftragsnummer in sorted(grouped):
        docs = tuple(
            sorted(grouped[auftragsnummer], key=lambda item: item.path.name)
        )
        recipients = tuple(recipients_by_auftrag.get(auftragsnummer, ()))
        if not recipients:
            blocked.append(
                BlockedBatch(
                    auftragsnummer=auftragsnummer,
                    reason="aucun Empfänger actif",
                    documents=docs,
                )
            )
            continue
        batches.append(
            MailBatch(
                auftragsnummer=auftragsnummer,
                documents=docs,
                recipients=recipients,
            )
        )

    return BatchBuildResult(tuple(batches), tuple(blocked))

