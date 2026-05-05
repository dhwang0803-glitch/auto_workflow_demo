"""Tests for StubEmbeddingBackend (PLAN_12 W3-3).

The stub stands in for BGE-M3 in local dev / pytest where pulling
sentence-transformers + torch (~2 GB) would be wasteful. These tests
guard the contract: 1024-dim, deterministic per input, L2-normalized.
"""
from __future__ import annotations

import math

import pytest

from app.backends.stub_embedding import StubEmbeddingBackend


@pytest.fixture
def backend() -> StubEmbeddingBackend:
    return StubEmbeddingBackend()


def test_dimension_matches_bge_m3(backend: StubEmbeddingBackend) -> None:
    # The DB column policy_extractions.embedding is VECTOR(1024) — both
    # backends MUST agree on this so a stub-trained dev workspace stays
    # insertable when the live BGE-M3 backend takes over.
    assert backend.dimension == 1024


async def test_returns_one_vector_per_input(backend: StubEmbeddingBackend) -> None:
    out = await backend.embed(["alpha", "beta", "gamma"])
    assert len(out) == 3
    for v in out:
        assert len(v) == 1024


async def test_deterministic_across_calls(backend: StubEmbeddingBackend) -> None:
    a1 = await backend.embed(["refund policy"])
    a2 = await backend.embed(["refund policy"])
    assert a1 == a2, "same input must produce identical vectors across calls"


async def test_different_inputs_produce_different_vectors(
    backend: StubEmbeddingBackend,
) -> None:
    a = await backend.embed(["refund policy"])
    b = await backend.embed(["refund policies"])  # one char different
    assert a != b, "different inputs must produce different vectors"


async def test_vectors_are_unit_normalized(backend: StubEmbeddingBackend) -> None:
    # L2 norm must be 1.0 within float64 precision — downstream cosine
    # similarity assumes unit vectors (dot product == cosine).
    out = await backend.embed(["any text", "", "x"])
    for v in out:
        norm = math.sqrt(sum(x * x for x in v))
        assert norm == pytest.approx(1.0, abs=1e-9)


async def test_empty_string_does_not_crash(backend: StubEmbeddingBackend) -> None:
    # Whitespace-only / empty-after-strip chunks are dropped upstream by
    # document_parser, but if one slips through we must not raise.
    out = await backend.embed([""])
    assert len(out) == 1
    assert len(out[0]) == 1024


async def test_empty_batch_returns_empty_list(backend: StubEmbeddingBackend) -> None:
    assert await backend.embed([]) == []


async def test_aclose_is_idempotent(backend: StubEmbeddingBackend) -> None:
    await backend.aclose()
    await backend.aclose()


async def test_ready_is_true(backend: StubEmbeddingBackend) -> None:
    assert await backend.ready() is True
