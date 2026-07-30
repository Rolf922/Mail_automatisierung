from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from pruefversand.models import ProtokollStatus
from pruefversand.pdf_tools import inspect_pdf
from pruefversand.scanner import scan_directory
from scripts.create_sample_pdf import build_pdf_bytes


PATTERN = (
    r"^(?P<auftragsnummer>[A-Za-z0-9]+)_"
    r"(?P<description>.+)\.pdf$"
)


class PdfAndScannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_valid_pdf_extracts_auftragsnummer_and_hash(self) -> None:
        path = self.root / "00125083_F001.pdf"
        path.write_bytes(build_pdf_bytes("synthetic"))

        inspection = inspect_pdf(path, PATTERN)

        self.assertTrue(inspection.is_valid)
        self.assertEqual(inspection.auftragsnummer, "00125083")
        self.assertEqual(len(inspection.sha256 or ""), 64)

    def test_ambiguous_f0661_never_becomes_ready(self) -> None:
        path = self.root / "F0661.pdf"
        path.write_bytes(build_pdf_bytes("synthetic"))

        first = scan_directory(self.root, PATTERN)
        second = scan_directory(self.root, PATTERN, first.observations)

        self.assertEqual(first.items[0].status, ProtokollStatus.DISCOVERED)
        self.assertEqual(
            second.items[0].status,
            ProtokollStatus.MANUAL_REVIEW,
        )

    def test_two_identical_scans_are_required_for_ready(self) -> None:
        path = self.root / "126021_F0661.pdf"
        path.write_bytes(build_pdf_bytes("synthetic"))

        first = scan_directory(self.root, PATTERN)
        second = scan_directory(self.root, PATTERN, first.observations)

        self.assertEqual(first.items[0].status, ProtokollStatus.DISCOVERED)
        self.assertEqual(second.items[0].status, ProtokollStatus.READY)

    def test_modified_file_is_not_stable(self) -> None:
        path = self.root / "126021_F0661.pdf"
        path.write_bytes(build_pdf_bytes("version 1"))
        first = scan_directory(self.root, PATTERN)
        path.write_bytes(build_pdf_bytes("version 2 plus longue"))

        second = scan_directory(self.root, PATTERN, first.observations)

        self.assertEqual(second.items[0].status, ProtokollStatus.DISCOVERED)

    def test_fake_pdf_is_quarantined_after_stable_scan(self) -> None:
        path = self.root / "126021_F0661.pdf"
        path.write_bytes(b"not a pdf")
        first = scan_directory(self.root, PATTERN)
        second = scan_directory(self.root, PATTERN, first.observations)

        self.assertEqual(second.items[0].status, ProtokollStatus.QUARANTINED)
        self.assertEqual(len(second.items[0].observation.sha256 or ''), 64)


if __name__ == "__main__":
    unittest.main()
