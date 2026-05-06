"""Document parsing + chunking for the docs path of skill bootstrap.

Persona B (5-person team, handbook PDF) uploads a file → API_Server stores it
in `policy_documents` → this service turns the raw bytes into chunks ready
for embedding (W3-3) and policy extraction (W3-4 / Phase D vision).

Three input formats:

- application/pdf  — text via pypdf, page image via pypdfium2 (Phase C)
- text/markdown    — passed through as-is, no image
- text/plain       — passed through as-is, no image

Phase C multimodal pivot: PDF chunks now carry an `image` data URL holding
the source page rendered to PNG, so policy_extract can pass it alongside
the chunk text. Chunking is page-aware for PDFs — each chunk lives inside
exactly one source page so the per-chunk image is well-defined. For text
and markdown there's no native page concept and `image` stays None.

Chunking strategy: sliding char window with paragraph-boundary preference.
Each chunk is at most `chunk_size` characters; a `chunk_overlap` carry-over
between adjacent chunks keeps a policy that straddles a boundary visible to
retrieval. When a window-end falls inside the last quarter of the window, we
snap it back to the nearest blank-line break so chunks don't slice
mid-sentence when avoidable.
"""
from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from typing import Iterable

import pypdfium2 as pdfium
from pypdf import PdfReader


@dataclass(frozen=True)
class DocumentChunk:
    """One ready-to-embed slice of an uploaded document.

    `image` is a base64 data URL (`data:image/png;base64,...`) carrying the
    source PDF page rendered to PNG. It's the input for the multimodal
    policy_extract path. None for text/markdown inputs and for PDF pages
    that happen to chunk down to nothing (those don't produce chunks at
    all). The data URL form is what `LlamaCppGemmaBackend` expects in its
    `images` argument — no caller-side encoding required.
    """

    index: int
    text: str
    image: str | None = None


class UnsupportedMimeTypeError(ValueError):
    """The uploaded file's mime_type is not one of the supported formats."""


# ~800 chars ≈ 200 tokens — leaves room for system prompt + few-shot in the
# downstream policy_extract call (PLAN_12 §6 budget).
DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 100

# Render scale for PDF pages. 1.5 yields ~108 DPI for letter-size pages
# (918x1188 px), readable for OCR by Gemma's vision encoder without
# blowing up the in-memory base64 string. Gemma's vision tower resizes
# internally so going higher rarely improves recall — kept tunable in
# case Phase D / E tells us otherwise.
PDF_RENDER_SCALE = 1.5

SUPPORTED_MIME_TYPES = frozenset(
    {"application/pdf", "text/markdown", "text/plain"}
)


def parse_document(
    content: bytes,
    mime_type: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[DocumentChunk]:
    if mime_type not in SUPPORTED_MIME_TYPES:
        raise UnsupportedMimeTypeError(
            f"mime_type {mime_type!r} not in {sorted(SUPPORTED_MIME_TYPES)}"
        )
    if chunk_size <= 0 or chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError(
            f"invalid chunking: chunk_size={chunk_size}, "
            f"overlap={chunk_overlap} (must be 0 <= overlap < chunk_size)"
        )

    if mime_type == "application/pdf":
        return _parse_pdf(content, chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    # markdown + plain text decode the same way. utf-8 is required — we do
    # not silently strip bytes that fail to decode, since that would produce
    # mismatched embeddings against the original document.
    text = content.decode("utf-8")
    text_chunks = _chunk_text(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    return [
        DocumentChunk(index=i, text=t, image=None)
        for i, t in enumerate(text_chunks)
    ]


def _parse_pdf(
    content: bytes, *, chunk_size: int, chunk_overlap: int
) -> list[DocumentChunk]:
    """PDF path: per-page text extraction + per-page image render.

    pypdf does the text extraction (already in deps, BSD-3-Clause).
    pypdfium2 does the image render (Apache-2.0, separate library so we
    don't pull in PyMuPDF's AGPL into a hackathon submission). The two
    libraries open their own copies of the document — page indices align
    because they both use the natural sequential page order.

    Each page is chunked independently. Every resulting chunk carries the
    same image data URL as its source page. Empty pages (no extractable
    text) produce zero chunks rather than a chunk with text="" — image-
    only pages would force the LLM to hallucinate from pixels alone, and
    Phase D will revisit if that turns out to be a real loss.
    """
    reader = PdfReader(io.BytesIO(content))
    document = pdfium.PdfDocument(content)

    if len(document) != len(reader.pages):
        # pypdf and pypdfium2 disagree on page count — exotic input we don't
        # want to silently handle. Raising lets the caller surface a clean
        # 4xx instead of producing garbage chunks.
        raise ValueError(
            f"PDF page count mismatch: pypdf={len(reader.pages)} "
            f"pypdfium2={len(document)}"
        )

    out: list[DocumentChunk] = []
    next_index = 0
    for page_no, page in enumerate(reader.pages):
        page_text = (page.extract_text() or "").strip()
        if not page_text:
            continue
        page_image = _render_page_to_data_url(document[page_no])
        for chunk_text in _chunk_text(
            page_text, chunk_size=chunk_size, chunk_overlap=chunk_overlap
        ):
            out.append(
                DocumentChunk(
                    index=next_index,
                    text=chunk_text,
                    image=page_image,
                )
            )
            next_index += 1
    return out


def _render_page_to_data_url(page: pdfium.PdfPage) -> str:
    """Render a single PDF page to a base64 PNG data URL.

    The data URL form (`data:image/png;base64,...`) is what
    `LlamaCppGemmaBackend._chat_payload` and the Anthropic backend both
    consume directly — no further wrapping needed at any call site.

    Memory budget note: a typical handbook page renders to ~200-300 KB
    of PNG, ~280-400 KB of base64. With 8 pages × ~3 chunks/page that's
    ~10 MB of duplicated image strings sitting in DocumentChunk records
    in the worst case. Acceptable for an in-process pipeline; if we ever
    persist these to Postgres we'll dedupe by page (Phase D / E concern).
    """
    pil_image = page.render(scale=PDF_RENDER_SCALE).to_pil()
    buf = io.BytesIO()
    pil_image.save(buf, format="PNG", optimize=True)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _chunk_text(
    text: str, *, chunk_size: int, chunk_overlap: int
) -> list[str]:
    """Char-window chunker with paragraph-boundary snapping. Returns plain
    strings — the caller wraps them in DocumentChunk so it can attach
    page-image metadata (PDF) or leave it None (markdown/text)."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end < len(text):
            # Snap to the last paragraph break inside the window's tail
            # quarter so chunks prefer natural boundaries when one is nearby.
            window_min = start + (chunk_size * 3) // 4
            split = text.rfind("\n\n", window_min, end)
            if split > start:
                end = split
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - chunk_overlap, start + 1)

    return chunks
