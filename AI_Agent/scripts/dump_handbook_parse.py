"""Print every chunk extracted from the handbook fixture for human review.

Use this when you want to eyeball the parser's output rather than rely on
the assertions in tests/test_document_parser.py — automated checks confirm
shape but cannot tell you whether the chunks read like sensible chunks.

Usage:
    python scripts/dump_handbook_parse.py            # prints all chunks
    python scripts/dump_handbook_parse.py --noise    # only print chunks
                                                       containing browser
                                                       print banners
"""
from __future__ import annotations

import sys
from pathlib import Path

from app.services.document_parser import parse_document

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "gitlab_handbook_excerpt.pdf"


def main() -> int:
    noise_only = "--noise" in sys.argv

    pdf = FIXTURE.read_bytes()
    chunks = parse_document(pdf, "application/pdf")

    print(f"fixture: {FIXTURE}")
    print(f"size:    {len(pdf):,} bytes")
    print(f"chunks:  {len(chunks)}")
    if chunks:
        lengths = [len(c.text) for c in chunks]
        print(
            f"length:  min={min(lengths)}  max={max(lengths)}  "
            f"mean={sum(lengths) // len(lengths)}"
        )
    print()

    for c in chunks:
        if noise_only and "file:///" not in c.text:
            continue
        print(f"=== chunk #{c.index}  ({len(c.text)} chars) ===")
        print(c.text)
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
