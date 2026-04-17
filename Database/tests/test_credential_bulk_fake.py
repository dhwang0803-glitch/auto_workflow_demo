"""PLAN_09 — InMemoryCredentialStore bulk_retrieve + type tests.

Fake-based unit tests — no DB required. The fake mirrors the Postgres
impl's contract (ownership filter + partial-fail-raises + empty allowed)
so downstream callers (API_Server PLAN_07) can rely on either backend.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from tests.fakes import InMemoryCredentialStore


@pytest.fixture
def store():
    return InMemoryCredentialStore()


async def test_fake_store_preserves_type(store):
    owner = uuid4()
    cid = await store.store(
        owner, "smtp-primary", {"user": "u", "password": "p"},
        credential_type="smtp",
    )
    assert store._peek_type(cid) == "smtp"


async def test_fake_store_default_type_unknown(store):
    owner = uuid4()
    cid = await store.store(owner, "legacy", {"k": "v"})
    assert store._peek_type(cid) == "unknown"


async def test_fake_bulk_retrieve_happy(store):
    owner = uuid4()
    cid1 = await store.store(owner, "a", {"v": 1}, credential_type="smtp")
    cid2 = await store.store(owner, "b", {"v": 2}, credential_type="postgres_dsn")
    cid3 = await store.store(owner, "c", {"v": 3}, credential_type="slack_webhook")

    got = await store.bulk_retrieve([cid1, cid2, cid3], owner_id=owner)

    assert got == {cid1: {"v": 1}, cid2: {"v": 2}, cid3: {"v": 3}}


async def test_fake_bulk_retrieve_ownership_filter(store):
    owner_a = uuid4()
    owner_b = uuid4()
    cid_a = await store.store(owner_a, "mine", {"v": "a"})

    with pytest.raises(KeyError, match="missing credential"):
        await store.bulk_retrieve([cid_a], owner_id=owner_b)


async def test_fake_bulk_retrieve_missing_id_raises(store):
    owner = uuid4()
    cid_real = await store.store(owner, "real", {"v": 1})
    cid_fake = uuid4()

    with pytest.raises(KeyError, match="missing credential"):
        await store.bulk_retrieve([cid_real, cid_fake], owner_id=owner)


async def test_fake_bulk_retrieve_empty_list(store):
    owner = uuid4()
    got = await store.bulk_retrieve([], owner_id=owner)
    assert got == {}


# PLAN_10 — list_by_owner on fake


async def test_fake_list_by_owner_happy(store):
    import asyncio

    owner = uuid4()
    cid1 = await store.store(owner, "a", {"v": 1}, credential_type="smtp")
    await asyncio.sleep(0.001)
    cid2 = await store.store(owner, "b", {"v": 2}, credential_type="slack_webhook")

    rows = await store.list_by_owner(owner)

    assert [r.id for r in rows] == [cid2, cid1]  # DESC
    assert [r.name for r in rows] == ["b", "a"]
    assert [r.type for r in rows] == ["slack_webhook", "smtp"]


async def test_fake_list_by_owner_empty(store):
    rows = await store.list_by_owner(uuid4())
    assert rows == []


async def test_fake_list_by_owner_ownership_filter(store):
    owner_a = uuid4()
    owner_b = uuid4()
    await store.store(owner_a, "a-secret", {"v": 1})
    await store.store(owner_b, "b-secret", {"v": 2})

    rows_a = await store.list_by_owner(owner_a)
    assert len(rows_a) == 1
    assert rows_a[0].name == "a-secret"
