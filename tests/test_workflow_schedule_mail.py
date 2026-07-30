from __future__ import annotations

from datetime import date, datetime, time, timezone
from pathlib import Path
import tempfile
import unittest

from pruefversand.mock_mail import (
    MailSafetyError,
    MockMailAmbiguousTimeout,
    MockMailProvider,
)
from pruefversand.models import ReadyDocument, Recipient
from pruefversand.schedule import is_weekly_run_due
from pruefversand.workflow import build_batches
from scripts.create_sample_pdf import build_pdf_bytes


class WorkflowTests(unittest.TestCase):
    def test_groups_only_documents_of_same_auftrag(self) -> None:
        documents = (
            ReadyDocument(Path("b.pdf"), "126021", "b" * 64, 10),
            ReadyDocument(Path("a.pdf"), "126021", "a" * 64, 10),
            ReadyDocument(Path("c.pdf"), "999999", "c" * 64, 10),
        )
        recipients = {
            "126021": (Recipient("a@example.invalid", "TO"),),
        }

        result = build_batches(documents, recipients)

        self.assertEqual(len(result.batches), 1)
        self.assertEqual(
            [item.path.name for item in result.batches[0].documents],
            ["a.pdf", "b.pdf"],
        )
        self.assertEqual(result.blocked[0].auftragsnummer, "999999")

    def test_weekly_run_is_due_once_per_period(self) -> None:
        friday_after = datetime(
            2026, 7, 31, 15, 5, tzinfo=timezone.utc
        )
        monday = date(2026, 7, 27)

        self.assertTrue(
            is_weekly_run_due(friday_after, 4, time(15, 0), None)
        )
        self.assertFalse(
            is_weekly_run_due(friday_after, 4, time(15, 0), monday)
        )


class MockMailTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.pdf = self.root / "126021_F0661.pdf"
        self.pdf.write_bytes(build_pdf_bytes("synthetic"))
        self.provider = MockMailProvider(
            self.root / "outbox",
            ("example.invalid",),
            maximum_attachment_bytes=1_000_000,
        )

    def test_writes_eml_without_network(self) -> None:
        result = self.provider.send(
            subject="Prüfprotokolle Auftrag 126021",
            body="Test",
            to=("kunde@example.invalid",),
            attachments=(self.pdf,),
        )

        self.assertTrue(result.message_path.is_file())
        content = result.message_path.read_bytes()
        self.assertIn(b"[TEST]", content)
        self.assertIn(b"126021_F0661.pdf", content)

    def test_uses_preallocated_correlation_id(self) -> None:
        correlation_id = '11111111-2222-3333-4444-555555555555'

        result = self.provider.send(
            subject='Test reprise',
            body='Test',
            to=('kunde@example.invalid',),
            attachments=(self.pdf,),
            correlation_id=correlation_id,
        )

        self.assertEqual(result.correlation_id, correlation_id)
        self.assertIn(correlation_id, result.message_path.name)

    def test_refuses_real_domain(self) -> None:
        with self.assertRaisesRegex(MailSafetyError, "interdit"):
            self.provider.send(
                subject="Test",
                body="Test",
                to=("client@example.com",),
            )

    def test_refuses_oversized_attachments(self) -> None:
        provider = MockMailProvider(
            self.root / "outbox",
            ("example.invalid",),
            maximum_attachment_bytes=1,
        )
        with self.assertRaisesRegex(MailSafetyError, "trop grandes"):
            provider.send(
                subject="Test",
                body="Test",
                to=("kunde@example.invalid",),
                attachments=(self.pdf,),
            )

    def test_accepts_total_exactly_equal_to_limit(self) -> None:
        second_pdf = self.root / '126021_F0662.pdf'
        second_pdf.write_bytes(build_pdf_bytes('synthetic boundary second'))
        total_size = self.pdf.stat().st_size + second_pdf.stat().st_size
        provider = MockMailProvider(
            self.root / 'equal-outbox',
            ('example.invalid',),
            maximum_attachment_bytes=total_size,
        )

        result = provider.send(
            subject='Test limite exacte',
            body='Test',
            to=('kunde@example.invalid',),
            attachments=(self.pdf, second_pdf),
        )

        self.assertTrue(result.message_path.is_file())

    def test_refuses_total_one_byte_over_limit(self) -> None:
        second_pdf = self.root / '126021_F0662.pdf'
        second_pdf.write_bytes(build_pdf_bytes('synthetic boundary second'))
        total_size = self.pdf.stat().st_size + second_pdf.stat().st_size
        outbox = self.root / 'one-byte-over-outbox'
        provider = MockMailProvider(
            outbox,
            ('example.invalid',),
            maximum_attachment_bytes=total_size - 1,
        )

        with self.assertRaisesRegex(
            MailSafetyError,
            f'{total_size} octets',
        ):
            provider.send(
                subject='Test limite depassee de un octet',
                body='Test',
                to=('kunde@example.invalid',),
                attachments=(self.pdf, second_pdf),
            )

        self.assertFalse(outbox.exists())

    def test_simulated_timeout_creates_no_eml(self) -> None:
        outbox = self.root / "timeout-outbox"
        provider = MockMailProvider(
            outbox,
            ("example.invalid",),
            maximum_attachment_bytes=1_000_000,
            simulate_timeout=True,
        )

        with self.assertRaises(MockMailAmbiguousTimeout) as raised:
            provider.send(
                subject="Test timeout ambigu",
                body="Test",
                to=("kunde@example.invalid",),
                attachments=(self.pdf,),
            )

        self.assertIn("timeout ambigu", raised.exception.reason)
        self.assertFalse(outbox.exists())


if __name__ == "__main__":
    unittest.main()
