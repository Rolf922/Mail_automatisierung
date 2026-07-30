from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import make_msgid
from pathlib import Path
import re
import uuid


class MailSafetyError(ValueError):
    pass


AMBIGUOUS_MOCK_TIMEOUT_REASON = (
    'timeout ambigu simule du fournisseur e-mail mock; '
    'verification humaine requise, aucun retry automatique'
)


class MockMailAmbiguousTimeout(TimeoutError):
    def __init__(self, correlation_id: str) -> None:
        super().__init__(AMBIGUOUS_MOCK_TIMEOUT_REASON)
        self.correlation_id = correlation_id
        self.reason = AMBIGUOUS_MOCK_TIMEOUT_REASON


@dataclass(frozen=True, slots=True)
class MockSendResult:
    correlation_id: str
    message_path: Path
    intended_recipients: tuple[str, ...]
    actual_recipients: tuple[str, ...]


def _recipient_domain(address: str) -> str:
    if address.count("@") != 1:
        raise MailSafetyError(f"adresse destinataire invalide : {address}")
    return address.rsplit("@", maxsplit=1)[1].lower()


class MockMailProvider:
    def __init__(
        self,
        outbox_path: str | Path,
        allowed_domains: tuple[str, ...],
        maximum_attachment_bytes: int,
        redirect_to: str = "",
        simulate_timeout: bool = False,
    ) -> None:
        self.outbox_path = Path(outbox_path)
        self.allowed_domains = tuple(domain.lower() for domain in allowed_domains)
        self.maximum_attachment_bytes = maximum_attachment_bytes
        self.redirect_to = redirect_to.strip()
        self.simulate_timeout = simulate_timeout

    def _validate_recipients(self, recipients: tuple[str, ...]) -> None:
        if not recipients:
            raise MailSafetyError("au moins un destinataire est obligatoire")
        for address in recipients:
            if _recipient_domain(address) not in self.allowed_domains:
                raise MailSafetyError(
                    f"domaine destinataire interdit en mode mock : {address}"
                )

    def send(
        self,
        *,
        subject: str,
        body: str,
        to: tuple[str, ...],
        cc: tuple[str, ...] = (),
        attachments: tuple[Path, ...] = (),
        correlation_id: str | None = None,
    ) -> MockSendResult:
        intended = to + cc
        self._validate_recipients(intended)

        actual_to = (self.redirect_to,) if self.redirect_to else to
        actual_cc: tuple[str, ...] = () if self.redirect_to else cc
        self._validate_recipients(actual_to + actual_cc)

        for path in attachments:
            if not path.is_file():
                raise MailSafetyError(f"pièce jointe introuvable : {path}")
        total_size = sum(path.stat().st_size for path in attachments)
        if total_size > self.maximum_attachment_bytes:
            raise MailSafetyError(
                f"pièces jointes trop grandes : {total_size} octets"
            )

        correlation_id = correlation_id or str(uuid.uuid4())
        if self.simulate_timeout:
            raise MockMailAmbiguousTimeout(correlation_id)
        message = EmailMessage()
        message["From"] = "pruefversand-home@example.invalid"
        message["To"] = ", ".join(actual_to)
        if actual_cc:
            message["Cc"] = ", ".join(actual_cc)
        message["Subject"] = f"[TEST] {subject}"
        message["Date"] = datetime.now(timezone.utc)
        message["Message-ID"] = make_msgid(domain="example.invalid")
        message["X-Pruefversand-Correlation-ID"] = correlation_id
        message["X-Pruefversand-Intended-To"] = ", ".join(intended)
        message.set_content(body)

        for path in attachments:
            with path.open("rb") as stream:
                message.add_attachment(
                    stream.read(),
                    maintype="application",
                    subtype="pdf",
                    filename=path.name,
                )

        self.outbox_path.mkdir(parents=True, exist_ok=True)
        safe_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_id = re.sub(r"[^A-Za-z0-9-]", "", correlation_id)
        output = self.outbox_path / f"{safe_stamp}_{safe_id}.eml"
        output.write_bytes(message.as_bytes(policy=SMTP))
        return MockSendResult(
            correlation_id=correlation_id,
            message_path=output,
            intended_recipients=intended,
            actual_recipients=actual_to + actual_cc,
        )
