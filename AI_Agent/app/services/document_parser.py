"""Document parsing + chunking for the docs path of skill bootstrap (PLAN_12 W3-2).

Persona B (5-person team, handbook PDF) uploads a file → API_Server stores it
in `policy_documents` → this service turns the raw bytes into chunks ready
for embedding (W3-3) and policy extraction (W3-4). Three input formats:

- application/pdf  — extracted with pypdf (pure-Python, MIT)
- text/markdown    — passed through as-is
- text/plain       — passed through as-is

Chunking strategy: sliding char window with paragraph-boundary preference.
Each chunk is at most `chunk_size` characters; a `chunk_overlap` carry-over
between adjacent chunks keeps a policy that straddles a boundary visible to
retrieval. When a window-end falls inside the last quarter of the window, we
snap it back to the nearest blank-line break so chunks don't slice
mid-sentence when avoidable. The resulting DocumentChunk records map 1:1
onto rows in `policy_extractions` (chunk_index + chunk_text — see PLAN_12
§5).
"""
from __future__ import annotations

import io
from dataclasses import dataclass

from pypdf import PdfReader


@dataclass(frozen=True)
class DocumentChunk:
    """One ready-to-embed slice of an uploaded document."""

    index: int
    text: str


class UnsupportedMimeTypeError(ValueError):
    """The uploaded file's mime_type is not one of the supported formats."""


# ~800 chars ≈ 200 tokens — leaves room for system prompt + few-shot in the
# downstream policy_extract call (PLAN_12 §6 budget).
DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 100

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

    text = _extract_text(content, mime_type)
    return _chunk_text(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)


def _extract_text(content: bytes, mime_type: str) -> str:
    if mime_type == "application/pdf":
        reader = PdfReader(io.BytesIO(content))
        # extract_text() returns "" for image-only pages — fine, downstream
        # _chunk_text discards whitespace-only output.
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(pages)
    # markdown + plain text decode the same way. utf-8 is required — we do
    # not silently strip bytes that fail to decode, since that would produce
    # mismatched embeddings against the original document.
    return content.decode("utf-8")


def _chunk_text(
    text: str, *, chunk_size: int, chunk_overlap: int
) -> list[DocumentChunk]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [DocumentChunk(index=0, text=text)]

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

    return [DocumentChunk(index=i, text=c) for i, c in enumerate(chunks)]
