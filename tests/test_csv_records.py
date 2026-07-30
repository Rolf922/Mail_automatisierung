from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from pruefversand.csv_records import validate_csv


HEADER = (
    "auftragsnummer,baustelle_name,kunde_name,kontakt_name,"
    "email,empfaengertyp,aktiv\n"
)


class CsvValidationTests(unittest.TestCase):
    def _write(self, content: str, *, bom: bool = False) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "input.csv"
        encoding = "utf-8-sig" if bom else "utf-8"
        path.write_text(content, encoding=encoding)
        return path

    def test_preserves_leading_zero_and_accepts_bom(self) -> None:
        path = self._write(
            HEADER
            + "00125083,Testbau,Test GmbH,Anna,"
            "anna@example.invalid,TO,1\n",
            bom=True,
        )

        result = validate_csv(path)

        self.assertTrue(result.is_valid)
        self.assertEqual(result.records[0].auftragsnummer, "00125083")

    def test_preserves_multiple_distinct_leading_zero_numbers(self) -> None:
        path = self._write(
            HEADER
            + '00991001,Baustelle Eins,Kunde HOME,Anna,'
            'anna.991001@example.invalid,TO,1\n'
            + '00991002,Baustelle Zwei,Kunde HOME,Ben,'
            'ben.991002@example.invalid,TO,1\n'
        )

        result = validate_csv(path)

        self.assertTrue(result.is_valid)
        self.assertEqual(
            [record.auftragsnummer for record in result.records],
            ['00991001', '00991002'],
        )

    def test_accepts_explicit_inactive_row(self) -> None:
        path = self._write(
            HEADER
            + '00991003,Baustelle CSV Gamma,Kunde Synth HOME,Dora Neu,'
            'dora.991003@example.invalid,TO,0\n'
        )

        result = validate_csv(path)

        self.assertTrue(result.is_valid)
        self.assertEqual(result.records[0].auftragsnummer, '00991003')
        self.assertFalse(result.records[0].aktiv)

    def test_invalid_email_blocks_the_row(self) -> None:
        path = self._write(
            HEADER
            + "126021,Testbau,Test GmbH,Anna,"
            "adresse-invalide,TO,1\n"
        )

        result = validate_csv(path)

        self.assertFalse(result.is_valid)
        self.assertEqual(result.records, ())
        self.assertEqual(result.issues[0].field, "email")

    def test_duplicate_recipient_is_reported(self) -> None:
        row = (
            "126021,Testbau,Test GmbH,Anna,"
            "anna@example.invalid,TO,1\n"
        )
        result = validate_csv(self._write(HEADER + row + row))

        self.assertFalse(result.is_valid)
        self.assertEqual(len(result.records), 1)
        self.assertIn("dupliqué", result.issues[0].message)


if __name__ == "__main__":
    unittest.main()
