"""Tests for BgeM3EmbeddingBackend (PLAN_12 W3-3).

The actual model load (~2 GB sentence-transformers download) only runs
on Modal — the dev image keeps EMBEDDING_BACKEND=stub. These tests
exercise the parts that DO run without sentence-transformers installed:

- Construction must not import sentence-transformers (the heavy import is
  inside _ensure_loaded, gated by lock).
- Dimension class attr matches the DB schema (1024).
- The `embed()` and `ready()` paths import sentence-transformers lazily —
  a `pytest.importorskip` lets the model-touching tests be skipped on dev
  installs without polluting the suite.
"""
from __future__ import annotations

import pytest

from app.backends.bge_embedding import BgeM3EmbeddingBackend


def test_dimension_class_attr_matches_db_schema() -> None:
    # `policy_extractions.embedding VECTOR(1024)` (PLAN_12 §5) is sized
    # for BGE-M3 — drift here breaks insert-time validation.
    assert BgeM3EmbeddingBackend.dimension == 1024


def test_construction_does_not_import_sentence_transformers() -> None:
    # The whole point of the lazy-load is so a dev container without
    # sentence-transformers can still run `from app.backends.bge_embedding
    # import BgeM3EmbeddingBackend` (e.g. via the container resolving the
    # bge_m3 setting). If construction triggered an import, this test file
    # itself would fail to load on the dev install.
    backend = BgeM3EmbeddingBackend()
    assert backend._model is None
    assert backend._model_name == "BAAI/bge-m3"


# --- live model tests (skipped unless sentence-transformers is installed) ---
#
# NOTE: importorskip at module-level skips the WHOLE module, including the
# unconditional tests above. Per-test skipif keeps the cheap tests runnable
# on dev installs while the heavy ones gate themselves.

try:
    import sentence_transformers  # noqa: F401

    _HAS_ST = True
except ImportError:
    _HAS_ST = False


_skip_no_st = pytest.mark.skipif(
    not _HAS_ST,
    reason="sentence-transformers not installed (dev image stays on stub)",
)


@pytest.fixture(scope="module")
def cpu_backend() -> BgeM3EmbeddingBackend:
    # Force cpu so the test runs anywhere sentence-transformers + torch
    # are installed, even without a GPU.
    return BgeM3EmbeddingBackend(device="cpu")


@_skip_no_st
async def test_live_embed_shape_and_normalization(
    cpu_backend: BgeM3EmbeddingBackend,
) -> None:
    import math

    out = await cpu_backend.embed(["refund within 30 days"])
    assert len(out) == 1
    assert len(out[0]) == 1024
    norm = math.sqrt(sum(x * x for x in out[0]))
    assert norm == pytest.approx(1.0, abs=1e-3), "BGE-M3 vectors must be L2-normalized"


@_skip_no_st
async def test_live_ready_loads_model(
    cpu_backend: BgeM3EmbeddingBackend,
) -> None:
    assert await cpu_backend.ready() is True
    assert cpu_backend._model is not None
