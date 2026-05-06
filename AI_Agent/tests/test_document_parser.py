"""Tests for document_parser (PLAN_12 W3-2 + Phase C multimodal).

The chunking + dispatch logic lives in this module — pypdf's text extraction
itself is its own concern. To exercise the PDF path without pulling reportlab
into dev deps, we hand-roll a minimal valid PDF carrying ASCII text via the
Helvetica builtin font. Validated against pypdf 5.x.

Phase C added per-page image rendering for the PDF path. Tests below
exercise both the contract (image is a base64 PNG data URL when source is
PDF, None otherwise) and the per-page mapping (chunks from the same source
page share an image data URL).
"""
from __future__ import annotations

import base64

import pytest

from app.services.document_parser import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    DocumentChunk,
    UnsupportedMimeTypeError,
    parse_document,
)


# --- minimal PDF helper ---------------------------------------------------


def _make_test_pdf(*lines: str) -> bytes:
    """Emit a single-page PDF carrying `lines` in 12pt Helvetica.

    Hand-rolled to avoid a reportlab dev-dep just for one fixture. The
    layout is deliberately minimalist (5 indirect objects) so xref offsets
    are easy to verify by inspection if pypdf ever rejects it.
    """
    parts: list[bytes] = []
    offsets: list[int] = []

    def _emit(b: bytes) -> None:
        parts.append(b)

    def _mark_object_start() -> None:
        offsets.append(sum(len(p) for p in parts))

    _emit(b"%PDF-1.4\n")

    _mark_object_start()
    _emit(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")

    _mark_object_start()
    _emit(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")

    _mark_object_start()
    _emit(
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R "
        b"/MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> "
        b"/Contents 4 0 R >>\nendobj\n"
    )

    body_parts: list[bytes] = [b"BT", b"/F1 12 Tf", b"14 TL", b"50 750 Td"]
    for i, line in enumerate(lines):
        if i > 0:
            body_parts.append(b"T*")
        escaped = (
            line.replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
        )
        body_parts.append(f"({escaped}) Tj".encode("latin-1"))
    body_parts.append(b"ET")
    body = b"\n".join(body_parts)

    _mark_object_start()
    _emit(f"4 0 obj\n<< /Length {len(body)} >>\nstream\n".encode())
    _emit(body)
    _emit(b"\nendstream\nendobj\n")

    _mark_object_start()
    _emit(
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 "
        b"/BaseFont /Helvetica >>\nendobj\n"
    )

    xref_offset = sum(len(p) for p in parts)
    _emit(b"xref\n0 6\n0000000000 65535 f \n")
    for off in offsets:
        _emit(f"{off:010d} 00000 n \n".encode())
    _emit(b"trailer\n<< /Size 6 /Root 1 0 R >>\n")
    _emit(f"startxref\n{xref_offset}\n%%EOF\n".encode())

    return b"".join(parts)


# --- text/markdown/plain dispatch ----------------------------------------


def test_plain_text_short_input_returns_single_chunk() -> None:
    chunks = parse_document(b"Refunds within 30 days.", "text/plain")
    assert chunks == [DocumentChunk(index=0, text="Refunds within 30 days.")]


def test_markdown_passes_through_as_one_chunk_when_short() -> None:
    md = b"# Refund Policy\n\nRefunds within **30 days**."
    chunks = parse_document(md, "text/markdown")
    assert len(chunks) == 1
    assert chunks[0].text.startswith("# Refund Policy")
    assert "**30 days**" in chunks[0].text


def test_empty_input_returns_no_chunks() -> None:
    assert parse_document(b"", "text/plain") == []


def test_whitespace_only_input_returns_no_chunks() -> None:
    assert parse_document(b"   \n\n  \t\n", "text/plain") == []


def test_unsupported_mime_type_raises() -> None:
    with pytest.raises(UnsupportedMimeTypeError):
        parse_document(b"hi", "application/zip")


def test_invalid_chunk_size_raises() -> None:
    with pytest.raises(ValueError):
        parse_document(b"hi", "text/plain", chunk_size=0)


def test_overlap_must_be_smaller_than_chunk_size() -> None:
    with pytest.raises(ValueError):
        parse_document(b"hi", "text/plain", chunk_size=100, chunk_overlap=100)


def test_non_utf8_text_raises() -> None:
    # Latin-1 bytes that are not valid utf-8 — we never silently strip.
    with pytest.raises(UnicodeDecodeError):
        parse_document(b"caf\xe9", "text/plain")


# --- chunking behavior ---------------------------------------------------


def test_long_text_splits_into_overlapping_chunks() -> None:
    # Single long paragraph (no \n\n) → falls through to char-window split.
    text = ("abc " * 500).strip()  # 1999 chars, no paragraph breaks
    chunks = parse_document(
        text.encode(), "text/plain", chunk_size=500, chunk_overlap=50
    )
    assert len(chunks) >= 4
    # Indexes are sequential starting at 0.
    assert [c.index for c in chunks] == list(range(len(chunks)))
    # Each non-final chunk is at most chunk_size characters.
    for c in chunks[:-1]:
        assert len(c.text) <= 500
    # Adjacent chunks share a tail — verify on the raw stream (whitespace-
    # tolerant since the parser .strip()s each chunk).
    for prev, curr in zip(chunks, chunks[1:]):
        # The overlap policy guarantees ≥1 shared character somewhere near
        # the boundary; we just check the next chunk starts inside the
        # previous chunk's last 60 chars (slack for paragraph snapping).
        assert curr.text[:20].strip() in prev.text or len(prev.text) < 100


def test_paragraph_boundary_snap_keeps_chunks_clean() -> None:
    # Two paragraphs, each ~400 chars. With chunk_size=500 the splitter
    # should end the first chunk at the blank-line boundary instead of
    # bleeding into paragraph two.
    para_a = "alpha " * 70  # ~420 chars
    para_b = "beta " * 80  # ~400 chars
    text = (para_a.strip() + "\n\n" + para_b.strip()).encode()
    chunks = parse_document(text, "text/plain", chunk_size=500, chunk_overlap=40)
    assert len(chunks) >= 2
    # First chunk should be predominantly "alpha", second predominantly "beta".
    assert chunks[0].text.count("alpha") > chunks[0].text.count("beta")
    assert chunks[-1].text.count("beta") > chunks[-1].text.count("alpha")


def test_default_chunking_constants_are_sane() -> None:
    # Guard against accidental regressions in the tunables.
    assert DEFAULT_CHUNK_SIZE > DEFAULT_CHUNK_OVERLAP > 0


# --- pdf path ------------------------------------------------------------


def test_pdf_with_text_extracts_lines() -> None:
    pdf = _make_test_pdf(
        "Refund policy",
        "All refunds within 30 days require manager approval.",
    )
    chunks = parse_document(pdf, "application/pdf")
    assert chunks, "expected at least one chunk from a non-empty PDF"
    joined = " ".join(c.text for c in chunks)
    assert "Refund policy" in joined
    assert "30 days" in joined


def test_pdf_with_no_text_returns_no_chunks() -> None:
    # Page declared but no BT/ET text operators — pypdf returns "" and we
    # treat the document as empty.
    pdf = _make_test_pdf()  # zero lines → just BT ... ET wrapper
    chunks = parse_document(pdf, "application/pdf")
    # The empty BT/ET still has the move-to operators but no Tj — extraction
    # returns whitespace only.
    assert chunks == []


# --- real-world fixture: gitlab handbook PDF ----------------------------
#
# Generated by `scripts/generate_handbook_fixture.py` from
# gitlab-com/content-sites/handbook (MIT) — see tests/fixtures/NOTICE.md
# for source URL, commit SHA, and license text. The hand-rolled minimal
# PDF above only exercises a happy-path single-page extraction. This
# fixture is the actual real-world shape: 8 pages, ~18 KB of substantive
# subscription/L&R policy text, plus the chromium-default print banners
# (URL/date/page-number) that real handbook PDFs carry.

import pathlib

_FIXTURE_PDF = (
    pathlib.Path(__file__).parent / "fixtures" / "gitlab_handbook_excerpt.pdf"
)


def _real_handbook_chunks() -> list[DocumentChunk]:
    return parse_document(_FIXTURE_PDF.read_bytes(), "application/pdf")


def test_real_handbook_fixture_present() -> None:
    # Loud failure if the fixture wasn't committed — the rest of the
    # real-handbook tests are meaningless without it.
    assert _FIXTURE_PDF.exists(), (
        f"Missing fixture {_FIXTURE_PDF}. Regenerate with "
        f"`python scripts/generate_handbook_fixture.py`."
    )


def test_real_handbook_chunk_count_in_reasonable_range() -> None:
    chunks = _real_handbook_chunks()
    # 18 KB of source text at ~800 char chunks ≈ 20-25 chunks. The bounds
    # are wide enough that a small upstream edit doesn't break the test
    # but tight enough to catch a chunking regression that produces 1
    # giant chunk or 200 micro-chunks.
    assert 10 <= len(chunks) <= 40, f"unexpected chunk count: {len(chunks)}"


def test_real_handbook_chunk_lengths_within_bounds() -> None:
    chunks = _real_handbook_chunks()
    for c in chunks:
        # No chunk exceeds the configured window.
        assert len(c.text) <= DEFAULT_CHUNK_SIZE, (
            f"chunk {c.index} length {len(c.text)} > {DEFAULT_CHUNK_SIZE}"
        )
    # No chunk is so tiny it's effectively useless for embedding (last
    # chunk gets a pass since it's the natural document tail).
    for c in chunks[:-1]:
        assert len(c.text) >= 100, (
            f"chunk {c.index} is suspiciously short ({len(c.text)} chars)"
        )


def test_real_handbook_subscription_vocabulary_preserved() -> None:
    # The page is about subscription licensing — these terms MUST survive
    # the markdown→HTML→print-PDF→pypdf round trip or the fixture is
    # useless for downstream policy extraction.
    chunks = _real_handbook_chunks()
    joined = " ".join(c.text for c in chunks).lower()
    for term in ("subscription", "license", "renewal", "support"):
        assert term in joined, f"expected vocabulary {term!r} missing"


def test_real_handbook_unique_phrase_survives_intact() -> None:
    # A multi-word phrase from the source (NOT inside a markdown link, so
    # it survives the renderer). Verifies word-order preservation — not
    # just that vocabulary leaked through somehow.
    chunks = _real_handbook_chunks()
    joined = " ".join(c.text for c in chunks)
    expected = "L&R Support Engineers help resolve problems"
    assert expected in joined, (
        f"phrase {expected!r} did not survive parsing intact"
    )


def test_real_handbook_adjacent_chunks_carry_overlap() -> None:
    # Verifies the overlap policy actually produces shared text at boundaries.
    # The first ~20 chars of chunk N+1, after stripping, should appear inside
    # chunk N (slack: paragraph snapping may shift the exact join point).
    chunks = _real_handbook_chunks()
    overlapping_pairs = 0
    for prev, curr in zip(chunks, chunks[1:]):
        head = curr.text[:20].strip()
        if head and head in prev.text:
            overlapping_pairs += 1
    # Most boundaries should overlap. A few may legitimately split on
    # paragraph breaks where the carry-over sits in whitespace.
    assert overlapping_pairs >= len(chunks) // 2, (
        f"only {overlapping_pairs}/{len(chunks) - 1} chunk boundaries "
        f"showed overlap — chunking may have lost its carry-over"
    )


def test_text_chunks_have_no_image() -> None:
    # Markdown / plain text inputs have no native page concept, so the
    # `image` field stays None across all chunks.
    md = b"# A\n\n" + (b"para body. " * 200)  # ~2.2 KB → multi-chunk
    chunks = parse_document(md, "text/markdown", chunk_size=400, chunk_overlap=40)
    assert len(chunks) >= 2
    assert all(c.image is None for c in chunks)


def test_pdf_chunks_carry_data_url_image() -> None:
    # Each PDF chunk must carry a base64 PNG data URL whose decoded payload
    # is a real PNG. We don't assert pixel content — only the shape of the
    # field, since rendering itself is pypdfium2's concern.
    pdf = _make_test_pdf(
        "Refund policy",
        "All refunds within 30 days require manager approval.",
    )
    chunks = parse_document(pdf, "application/pdf")
    assert chunks
    for c in chunks:
        assert c.image is not None
        assert c.image.startswith("data:image/png;base64,")
        b64 = c.image.split(",", 1)[1]
        png_bytes = base64.b64decode(b64)
        # PNG magic bytes — confirms pypdfium2 → PIL → save("PNG") wired up.
        assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
        # Sanity: a rendered handbook-shaped page is at least ~5 KB.
        assert len(png_bytes) >= 1024


def test_pdf_chunks_share_image_within_a_page() -> None:
    # All chunks produced from the same PDF page MUST hold the same image
    # data URL — that's the contract policy_extract relies on (per-chunk
    # image == its source page's render). Verified on the real handbook
    # fixture where individual pages routinely produce 2-4 chunks.
    chunks = _real_handbook_chunks()
    by_image: dict[str, list[int]] = {}
    for c in chunks:
        assert c.image is not None
        by_image.setdefault(c.image, []).append(c.index)
    # At least one image must appear for >1 chunk — otherwise per-page
    # chunking degenerated to one-chunk-per-page and we'd lose the
    # "shared image across chunks" invariant. The handbook excerpt has
    # enough text per page that this is guaranteed in practice.
    assert any(len(idxs) > 1 for idxs in by_image.values()), (
        "expected at least one page to produce multiple chunks sharing "
        "an image data URL"
    )
    # Distinct pages must produce distinct images. 8-page fixture, each
    # page should render to its own bytes — collisions would mean the
    # render path is somehow returning a constant.
    assert len(by_image) >= 2, (
        f"only {len(by_image)} distinct page images across {len(chunks)} "
        "chunks — render likely bound to wrong page"
    )


def test_pdf_image_only_page_produces_no_chunk() -> None:
    # A page with no extractable text — even with a valid render — produces
    # no DocumentChunk. Forcing an image-only chunk would push the LLM to
    # hallucinate text from pixels alone; downstream W3-3 / Phase D will
    # revisit if image-only pages turn out to carry signal we can't afford
    # to drop.
    pdf = _make_test_pdf()  # zero text lines → blank page text
    chunks = parse_document(pdf, "application/pdf")
    assert chunks == []


def test_real_handbook_browser_print_noise_is_bounded() -> None:
    # chromium's default print banner (URL + page N/M + date + page title)
    # gets re-emitted on every page. We accept that noise as the price of
    # real-world fixture realism — but it MUST stay a small fraction of
    # the document, otherwise downstream policy_extract will waste budget
    # on it. The page-number sentinel "8/8" (final page) is a deterministic
    # marker we can detect; we just bound how often any banner-shaped line
    # appears relative to substantive content.
    chunks = _real_handbook_chunks()
    joined = " ".join(c.text for c in chunks)
    total_chars = len(joined)
    # The banner reproduces the rendered HTML's file:// URL on every page.
    banner_chars = joined.count("file:///") * 80  # ~80-char banner line
    assert banner_chars / total_chars < 0.10, (
        f"browser print noise occupies {banner_chars / total_chars:.1%} "
        f"of the document — too high to leave un-stripped"
    )
