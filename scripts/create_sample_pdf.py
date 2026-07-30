from __future__ import annotations

from pathlib import Path


def build_pdf_bytes(text: str) -> bytes:
    escaped = (
        text.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )
    content = f"BT /F1 14 Tf 72 760 Td ({escaped}) Tj ET\n".encode("latin-1")
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R "
            b"/MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 4 0 R >> >> "
            b"/Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        (
            f"<< /Length {len(content)} >>\nstream\n".encode("ascii")
            + content
            + b"endstream"
        ),
    )

    document = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(document))
        document.extend(f"{number} 0 obj\n".encode("ascii"))
        document.extend(body)
        document.extend(b"\nendobj\n")

    xref_offset = len(document)
    document.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    document.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    document.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(document)


def main() -> int:
    project_root = Path(__file__).resolve().parent.parent
    target = (
        project_root
        / "sample_data"
        / "nas_mock"
        / "00125083_F001_Pruefprotokoll_2026-07-25.pdf"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(
        build_pdf_bytes(
            "SYNTHETISCHES PRUEFPROTOKOLL - AUFTRAG 00125083"
        )
    )
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

