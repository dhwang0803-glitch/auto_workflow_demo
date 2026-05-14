"""Unit tests for `app.services.personal_memory.PersonalMemoryPool`.

The pool is the read side of the Path-1 personalization design (memory
`project_personalization_memory_pattern.md`). The contract these tests
lock in:

  - The pool is forgiving on the cold-start path — every error mode
    on `load()` resolves to an empty pool, never an exception, because
    the agent's regression guard depends on `pool.size == 0` being
    safe to surface.
  - `search()` ranks active entries by cosine similarity and never
    returns the inactive ones, even if they are a closer match.
  - The user_id sanitization rejects anything that could escape the
    base directory on the filesystem.

We do NOT test `search()` against semantically meaningful vectors —
that would require a real embedding model. Instead we construct
explicit unit vectors (`[1,0,0,0]`, etc.) so the cosine ordering is
arithmetically obvious and the test is fast.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from app.services.personal_memory import (
    PersonalMemoryPool,
    PersonalMemoryWriteError,
    PersonalSkillEntry,
    upsert_personal_skill,
)


def _entry_dict(
    *,
    skill_id: str,
    embedding: list[float],
    condition: dict | None = None,
    action: dict | None = None,
    active: bool = True,
    suggestion_hash: str = "h",
    source: str = "hitl_edit",
    first_observed_at: str = "2026-05-09T00:00:00Z",
) -> dict:
    return {
        "id": skill_id,
        "condition": condition or {"text": f"cond-{skill_id}"},
        "action": action or {"text": f"act-{skill_id}"},
        "suggestion_hash": suggestion_hash,
        "embedding": embedding,
        "source": source,
        "first_observed_at": first_observed_at,
        "active": active,
    }


def _write_user_file(
    base_dir: Path, user_id: str, skills: list[dict]
) -> Path:
    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_dir / f"{user_id}.json"
    path.write_text(
        json.dumps(
            {
                "user_id": user_id,
                "version": "v1",
                "updated_at": "2026-05-09T00:00:00Z",
                "skills": skills,
                "reviews": [],
            }
        ),
        encoding="utf-8",
    )
    return path


# --- load(): cold-start variants -----------------------------------------


def test_empty_base_dir_returns_empty_pool() -> None:
    """`personal_memory_dir=""` is the feature-disabled path — the
    route still calls `load()`, the pool is just empty."""
    pool = PersonalMemoryPool.load("", "user-1")
    assert pool.size == 0


def test_none_base_dir_returns_empty_pool() -> None:
    pool = PersonalMemoryPool.load(None, "user-1")
    assert pool.size == 0


def test_none_user_id_returns_empty_pool(tmp_path: Path) -> None:
    """Anonymous request — no user_id means no file to read. The pool
    must still construct cleanly so the agent's tool-registration check
    sees `size==0`."""
    pool = PersonalMemoryPool.load(str(tmp_path), None)
    assert pool.size == 0


def test_missing_file_returns_empty_pool(tmp_path: Path) -> None:
    """First-ever request for a user whose file does not exist yet.
    No exception, no log noise — just an empty pool."""
    pool = PersonalMemoryPool.load(str(tmp_path), "brand-new-user")
    assert pool.size == 0


def test_malformed_file_returns_empty_pool_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A corrupt JSON file should not 502 the request. Operators get a
    warning in the log, the pool surfaces empty, and the agent skips
    retrieval as if it were a cold-start."""
    path = tmp_path / "user-corrupt.json"
    path.write_text("{not valid json", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="app.services.personal_memory"):
        pool = PersonalMemoryPool.load(str(tmp_path), "user-corrupt")
    assert pool.size == 0
    assert any(
        "personal_memory" in rec.message and "user-corrupt" in rec.message
        for rec in caplog.records
    )


def test_unsafe_user_id_rejected(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Path-traversal guard. The route hands us whatever the caller put
    on the wire; if a user sets `user_id=../../etc/passwd`, the
    sanitizer must refuse without touching the filesystem."""
    with caplog.at_level(logging.WARNING, logger="app.services.personal_memory"):
        pool = PersonalMemoryPool.load(str(tmp_path), "../etc/passwd")
    assert pool.size == 0
    assert any("rejecting unsafe user_id" in rec.message for rec in caplog.records)


# --- load(): happy path --------------------------------------------------


def test_normal_file_loads_active_entries(tmp_path: Path) -> None:
    """Pool size counts active entries only — inactive ones are
    filtered out at construction so `size` and `search()` agree on the
    domain."""
    skills = [
        _entry_dict(skill_id="a", embedding=[1.0, 0.0, 0.0, 0.0]),
        _entry_dict(skill_id="b", embedding=[0.0, 1.0, 0.0, 0.0]),
        _entry_dict(skill_id="c", embedding=[0.0, 0.0, 1.0, 0.0], active=False),
    ]
    _write_user_file(tmp_path, "user-1", skills)

    pool = PersonalMemoryPool.load(str(tmp_path), "user-1")
    assert pool.size == 2  # `c` filtered out

    # Query that perfectly matches `a` — active entries surface in
    # cosine order, inactive `c` never appears.
    hits = pool.search([1.0, 0.0, 0.0, 0.0], k=10)
    assert [h.id for h in hits] == ["a", "b"]


# --- search(): ordering + filtering --------------------------------------


def test_search_orders_by_cosine_similarity() -> None:
    """Direct construction (skipping load) so the test focuses on the
    ranking arithmetic. Vectors are unit-length already; cosine
    similarity reduces to a dot product, and the expected order falls
    out of the projections of each entry onto the query."""
    pool = PersonalMemoryPool(
        [
            PersonalSkillEntry(
                id="far",
                condition={},
                action={},
                suggestion_hash="",
                embedding=[0.0, 1.0, 0.0, 0.0],
                source="",
                first_observed_at="",
                active=True,
            ),
            PersonalSkillEntry(
                id="exact",
                condition={},
                action={},
                suggestion_hash="",
                embedding=[1.0, 0.0, 0.0, 0.0],
                source="",
                first_observed_at="",
                active=True,
            ),
            PersonalSkillEntry(
                id="middle",
                condition={},
                action={},
                suggestion_hash="",
                embedding=[0.7071, 0.7071, 0.0, 0.0],  # 45° from `exact`
                source="",
                first_observed_at="",
                active=True,
            ),
        ]
    )

    hits = pool.search([1.0, 0.0, 0.0, 0.0], k=3)
    assert [h.id for h in hits] == ["exact", "middle", "far"]


def test_search_excludes_inactive_entries_at_construction() -> None:
    """`active=False` entries never reach the search path — the pool
    drops them in `__init__`. This matters because the inactive flag
    is the soft-delete mechanism: an undone HITL approval should
    immediately stop scaffolding future extractions."""
    pool = PersonalMemoryPool(
        [
            PersonalSkillEntry(
                id="dropped",
                condition={},
                action={},
                suggestion_hash="",
                embedding=[1.0, 0.0],
                source="",
                first_observed_at="",
                active=False,
            ),
            PersonalSkillEntry(
                id="kept",
                condition={},
                action={},
                suggestion_hash="",
                embedding=[0.5, 0.5],
                source="",
                first_observed_at="",
                active=True,
            ),
        ]
    )
    assert pool.size == 1
    hits = pool.search([1.0, 0.0], k=5)
    # `dropped` would have scored 1.0 (perfect) but is inactive, so the
    # only remaining match is `kept` at 0.5.
    assert [h.id for h in hits] == ["kept"]


def test_search_returns_all_when_k_exceeds_pool_size() -> None:
    """`k > size` is a normal call shape — the agent asks for top-3 and
    the user only has 1 saved pattern. Return what we have, no error."""
    pool = PersonalMemoryPool(
        [
            PersonalSkillEntry(
                id="only",
                condition={},
                action={},
                suggestion_hash="",
                embedding=[1.0, 0.0],
                source="",
                first_observed_at="",
                active=True,
            ),
        ]
    )
    hits = pool.search([0.5, 0.5], k=10)
    assert len(hits) == 1
    assert hits[0].id == "only"


def test_search_empty_pool_returns_empty_list() -> None:
    """Defensive: the agent's tool registration guard means this branch
    isn't reached in production, but a direct call must still return
    a valid empty list rather than crash."""
    pool = PersonalMemoryPool([])
    assert pool.search([1.0, 0.0], k=3) == []


def test_search_drops_dimension_mismatch_entries() -> None:
    """Pre-migration files might store 768-dim vectors while the runtime
    embedder is 1024-dim. Skip the mismatched rows rather than 502.
    `size` still counts them so the agent does not see a transient
    zero — the next write cycle re-embeds and the rows become usable."""
    pool = PersonalMemoryPool(
        [
            PersonalSkillEntry(
                id="old-dim",
                condition={},
                action={},
                suggestion_hash="",
                embedding=[1.0, 0.0, 0.0],  # 3-dim — mismatched
                source="",
                first_observed_at="",
                active=True,
            ),
            PersonalSkillEntry(
                id="current-dim",
                condition={},
                action={},
                suggestion_hash="",
                embedding=[1.0, 0.0],  # 2-dim — matches query below
                source="",
                first_observed_at="",
                active=True,
            ),
        ]
    )
    assert pool.size == 2
    hits = pool.search([1.0, 0.0], k=5)
    assert [h.id for h in hits] == ["current-dim"]


# --- upsert_personal_skill (PR-I write side) ----------------------------


def _make_entry(
    skill_id: str = "s1",
    embedding: list[float] | None = None,
    *,
    active: bool = True,
    suggestion_hash: str = "h-s1",
    condition_text: str = "trigger when invoice arrives",
    action_text: str = "post to slack #finance",
) -> PersonalSkillEntry:
    return PersonalSkillEntry(
        id=skill_id,
        condition={"text": condition_text},
        action={"text": action_text},
        suggestion_hash=suggestion_hash,
        embedding=embedding or [1.0, 0.0, 0.0, 0.0],
        source="hitl_edit",
        first_observed_at="2026-05-14T00:00:00Z",
        active=active,
    )


async def test_upsert_creates_file_for_new_user(tmp_path: Path) -> None:
    """First write for a user creates the JSON file end-to-end and the
    pool reads back exactly one active row."""
    size = await upsert_personal_skill(
        base_dir=str(tmp_path),
        user_id="alice",
        entry=_make_entry(),
    )
    assert size == 1

    path = tmp_path / "alice.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["user_id"] == "alice"
    assert data["version"] == "v1"
    assert data["updated_at"]
    assert len(data["skills"]) == 1
    assert data["skills"][0]["id"] == "s1"

    pool = PersonalMemoryPool.load(str(tmp_path), "alice")
    assert pool.size == 1


async def test_upsert_appends_when_id_is_new(tmp_path: Path) -> None:
    """A second skill with a fresh id appends instead of replacing."""
    await upsert_personal_skill(
        base_dir=str(tmp_path),
        user_id="alice",
        entry=_make_entry(skill_id="s1"),
    )
    size = await upsert_personal_skill(
        base_dir=str(tmp_path),
        user_id="alice",
        entry=_make_entry(
            skill_id="s2",
            embedding=[0.0, 1.0, 0.0, 0.0],
        ),
    )
    assert size == 2
    data = json.loads((tmp_path / "alice.json").read_text(encoding="utf-8"))
    assert {row["id"] for row in data["skills"]} == {"s1", "s2"}


async def test_upsert_replaces_when_id_collides(tmp_path: Path) -> None:
    """Re-writing the same id (e.g. after the user edits the candidate
    pre-activate) keeps one row, not two — the dedup is on the in-file
    id list, not the suggestion_hash, so callers control it."""
    await upsert_personal_skill(
        base_dir=str(tmp_path),
        user_id="alice",
        entry=_make_entry(action_text="post to slack #finance"),
    )
    await upsert_personal_skill(
        base_dir=str(tmp_path),
        user_id="alice",
        entry=_make_entry(action_text="post to slack #ap-team"),
    )
    data = json.loads((tmp_path / "alice.json").read_text(encoding="utf-8"))
    assert len(data["skills"]) == 1
    assert data["skills"][0]["action"]["text"] == "post to slack #ap-team"


async def test_upsert_isolates_users(tmp_path: Path) -> None:
    """alice's writes never bleed into bob's file — the per-user file
    boundary is the user-scoping mechanism the regression guard depends
    on (PLAN_14 §4.6)."""
    await upsert_personal_skill(
        base_dir=str(tmp_path),
        user_id="alice",
        entry=_make_entry(),
    )
    await upsert_personal_skill(
        base_dir=str(tmp_path),
        user_id="bob",
        entry=_make_entry(skill_id="s-bob", suggestion_hash="h-bob"),
    )

    alice = PersonalMemoryPool.load(str(tmp_path), "alice")
    bob = PersonalMemoryPool.load(str(tmp_path), "bob")
    assert alice.size == 1
    assert bob.size == 1
    # The alice query vector matches her own entry, not bob's; we just
    # assert the bob file does not surface alice's id.
    assert {h.id for h in bob.search([1.0, 0.0, 0.0, 0.0], k=10)} == {"s-bob"}


async def test_upsert_inactive_entry_does_not_count_toward_pool_size(
    tmp_path: Path,
) -> None:
    """Re-activating after a soft-disable should round-trip: active=False
    sits in the file but doesn't show up in `size` — same surface
    `PersonalMemoryPool.__init__` filters on."""
    await upsert_personal_skill(
        base_dir=str(tmp_path),
        user_id="alice",
        entry=_make_entry(active=False),
    )
    pool = PersonalMemoryPool.load(str(tmp_path), "alice")
    assert pool.size == 0


async def test_upsert_rejects_unsafe_user_id(tmp_path: Path) -> None:
    """Path-traversal guard mirrors the load side."""
    with pytest.raises(PersonalMemoryWriteError, match="unsafe"):
        await upsert_personal_skill(
            base_dir=str(tmp_path),
            user_id="../etc/passwd",
            entry=_make_entry(),
        )


async def test_upsert_rejects_unconfigured_base_dir(tmp_path: Path) -> None:
    """Empty base_dir == feature disabled — the writer must refuse so
    the route can return 503 instead of silently creating files in the
    server's CWD."""
    with pytest.raises(PersonalMemoryWriteError, match="not configured"):
        await upsert_personal_skill(
            base_dir="",
            user_id="alice",
            entry=_make_entry(),
        )


async def test_upsert_recovers_from_corrupt_existing_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """If the file on disk is malformed (rare — write was atomic), we
    quarantine and start fresh rather than refusing every future write."""
    target = tmp_path / "alice.json"
    target.write_text("{not valid json", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="app.services.personal_memory"):
        size = await upsert_personal_skill(
            base_dir=str(tmp_path),
            user_id="alice",
            entry=_make_entry(),
        )
    assert size == 1
    assert (tmp_path / "alice.json.corrupt").exists()
    assert any("quarantining" in rec.message for rec in caplog.records)
