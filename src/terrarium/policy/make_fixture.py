"""Write `fixture_policy.pdf`, the committed document `test_extract.py` runs against.

    uv run python -m terrarium.policy.make_fixture

Generated rather than hand-committed as opaque bytes, and generated **with the pathology
the real document has**: the Punjab Clean Air Action Plan's font encoding puts a text-
positioning operator between many of its letters, so extracted text comes out as

    "The P unja b Clea n A ir Act ion P la n"

A clean fixture would let the quote guard pass by exact string match and prove nothing —
the whole reason `despace` exists is that exact matching fails on the real thing. So the
fixture is built with the same shredding, using PDF's `TJ` operator with kerning offsets,
which is exactly how the real document does it.

Uncompressed, so the file is readable in a diff. It is a few kilobytes.
"""

from __future__ import annotations

import sys
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / "fixture_policy.pdf"

# The sentences the fixture commits to. Chosen to exercise the mapping in `to_plan.py`:
# one traffic measure with a figure, one greening measure with a figure, one greening
# measure without one, and two that neither lever can express — which is the ratio the
# real document has and the reason `Coverage` reports a miss rate.
LINES = [
    "Punjab Clean Air Action Plan (test fixture)",
    "",
    "Source apportionment for Lahore identifies diesel vehicles at 28 percent and",
    "two-stroke exhaust at 8 percent of measured high PM2.5 concentrations.",
    "",
    "Measure 1: Establish a low emission zone removing 36 percent of vehicle",
    "emissions in the central district by 2030.",
    "",
    "Measure 2: Increase urban tree canopy cover by 25 percent across the",
    "metropolitan area through a sustained plantation drive.",
    "",
    "Measure 3: Undertake avenue plantation along major arterial roads.",
    "",
    "Measure 4: Reduce sulfur content in diesel fuel to 10 parts per million",
    "under revised fuel quality standards.",
    "",
    "Measure 5: Mandate catalytic converters on all newly registered vehicles",
    "and establish an annual inspection and certification regime.",
]


# Thousandths of an em, subtracted from the advance. Extractors insert a space when a
# glyph is displaced beyond a threshold, and the threshold is roughly a space's own width —
# a small kern is reassembled cleanly and proves nothing. -260 is past it, which is what
# reproduces the real document's "P unja b Clea n A ir" and gives the guard something to
# actually cope with.
_KERN = -260


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _shredded(text: str) -> str:
    """One line as a `TJ` array with a kerning offset between every pair of characters.

    This is what reproduces the real document's failure. A PDF text extractor reading
    `[(H) -60 (e) -60 (l) -60 (l) -60 (o)] TJ` sees five separately positioned glyphs and
    inserts spaces between them, because a negative kern is how a PDF says "a space goes
    here" — and it cannot tell a tight kern from a word break.
    """
    parts = " ".join(f"({_escape(ch)}) {_KERN}" for ch in text)
    return f"[{parts}] TJ"


def build() -> bytes:
    """A minimal one-page uncompressed PDF whose text extracts shredded."""
    lines = [
        "BT",
        "/F1 11 Tf",
        "40 780 Td",
        "16 TL",
    ]
    for line in LINES:
        lines.append(_shredded(line) if line else "()Tj")
        lines.append("T*")
    lines.append("ET")
    stream = "\n".join(lines).encode("latin-1")

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()

    return bytes(out)


def main() -> int:
    FIXTURE.write_bytes(build())
    print(f"wrote {FIXTURE} ({FIXTURE.stat().st_size} bytes)")

    from pypdf import PdfReader

    text = "".join(page.extract_text() or "" for page in PdfReader(str(FIXTURE)).pages)
    print("\nExtracted text, which is the point — this is what the guard has to cope with:\n")
    print(text[:400])
    return 0


if __name__ == "__main__":
    sys.exit(main())
