"""HTTP route tests for /v1/personalization/memory/upsert (PR-I).

The unit tests in `test_personal_memory.py` already cover the writer's
filesystem behavior; here we lock in the wire shape: settings gate,
embedding-source switch, validation, and the round-trip through
`PersonalMemoryPool.load` so the route truly closes the read/write loop.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.backends.stub_embedding import StubEmbeddingBackend
from app.main import create_app
from app.services.personal_memory import PersonalMemoryPool


class _RecordingEmbedding(StubEmbeddingBackend):
    """Stub backend that records every embed() input so tests can assert
    the route built the correct text from condition + action."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return await super().embed(texts)


def _skill_payload(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": "s1",
        "condition": {"text": "trigger when invoice arrives"},
        "action": {"text": "post to slack #finance"},
        "suggestion_hash": "h-s1",
        "source": "hitl_edit",
        "first_observed_at": "2026-05-14T00:00:00Z",
        "active": True,
    }
    base.update(overrides)
    return base


@pytest.fixture
def memory_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Activate the writer feature for the duration of one test.

    The route reads `Settings.personal_memory_dir` at request time via
    DI, so monkeypatching the env (which Settings reads on construction)
    is the clean way to flip the gate without subclassing Settings.
    """
    monkeypatch.setenv("PERSONAL_MEMORY_DIR", str(tmp_path))
    return tmp_path


# --- happy path: round-trips through the pool --------------------------------


@pytest.mark.asyncio
async def test_upsert_persists_and_pool_reads_back(memory_dir: Path) -> None:
    embedding = _RecordingEmbedding()
    app = create_app(embedding_override=embedding)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/personalization/memory/upsert",
            json={"user_id": "alice", "skill": _skill_payload()},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "ok": True,
        "pool_size": 1,
        "embedding_source": "server",
    }

    # The text the route handed to the embedding backend = condition.text
    # + " " + action.text (the same surface the search tool uses), so
    # cosine geometry stays consistent across read and write.
    assert embedding.calls == [
        ["trigger when invoice arrives post to slack #finance"]
    ]

    # Round-trip — the writer + pool must agree on the JSON shape.
    pool = PersonalMemoryPool.load(str(memory_dir), "alice")
    assert pool.size == 1


# --- embedding source switch -------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_uses_caller_embedding_when_provided(
    memory_dir: Path,
) -> None:
    """When the caller pins a vector (deterministic test path), the
    server skips the embedding backend entirely."""
    embedding = _RecordingEmbedding()
    app = create_app(embedding_override=embedding)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/personalization/memory/upsert",
            json={
                "user_id": "alice",
                "skill": _skill_payload(embedding=[1.0, 0.0, 0.0, 0.0]),
            },
        )

    assert resp.status_code == 200
    assert resp.json()["embedding_source"] == "caller"
    assert embedding.calls == []  # backend not consulted


# --- settings gate -----------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_returns_503_when_feature_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty `personal_memory_dir` is the production "feature off"
    sentinel — the route must surface 503 so API_Server can log a
    deployment-side warning instead of pretending the write succeeded."""
    monkeypatch.delenv("PERSONAL_MEMORY_DIR", raising=False)
    app = create_app(embedding_override=StubEmbeddingBackend())
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/personalization/memory/upsert",
            json={"user_id": "alice", "skill": _skill_payload()},
        )

    assert resp.status_code == 503
    assert "personal_memory_dir" in resp.json()["detail"]


# --- validation --------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_rejects_unsafe_user_id(memory_dir: Path) -> None:
    app = create_app(embedding_override=StubEmbeddingBackend())
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/personalization/memory/upsert",
            json={"user_id": "../etc/passwd", "skill": _skill_payload()},
        )

    assert resp.status_code == 422
    assert "unsafe" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_upsert_requires_skill_id(memory_dir: Path) -> None:
    """Pydantic-level validation — empty id is structurally wrong, the
    422 must come back before the writer is invoked."""
    app = create_app(embedding_override=StubEmbeddingBackend())
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/personalization/memory/upsert",
            json={"user_id": "alice", "skill": _skill_payload(id="")},
        )

    assert resp.status_code == 422
