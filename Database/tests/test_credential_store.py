"""FernetCredentialStore integration tests — PLAN_02 §6."""
from __future__ import annotations

import os
from uuid import uuid4

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("asyncpg")
cryptography = pytest.importorskip("cryptography")

from cryptography.fernet import Fernet, InvalidToken

from auto_workflow_database.models.core import User as UserORM
from auto_workflow_database.repositories._session import build_engine, build_sessionmaker
from auto_workflow_database.repositories.credential_store import FernetCredentialStore

DATABASE_URL = os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set — credential store integration test requires live DB",
)


@pytest.fixture
async def sm():
    engine = build_engine(DATABASE_URL)
    try:
        yield build_sessionmaker(engine)
    finally:
        await engine.dispose()


async def _seed_user(sm) -> UserORM:
    async with sm() as s, s.begin():
        u = UserORM(email=f"{uuid4()}@test.local", plan_tier="light")
        s.add(u)
        await s.flush()
        return u


async def test_store_retrieve_roundtrip(sm):
    user = await _seed_user(sm)
    store = FernetCredentialStore(sm, master_key=Fernet.generate_key())
    secret = {"slack_token": "xoxb-plan02-test"}

    cid = await store.store(user.id, "slack", secret)
    got = await store.retrieve(cid)
    assert got == secret

    await store.delete(cid)


async def test_wrong_key_rejects_ciphertext(sm):
    user = await _seed_user(sm)
    store_a = FernetCredentialStore(sm, master_key=Fernet.generate_key())
    cid = await store_a.store(user.id, "k", {"v": 1})

    store_b = FernetCredentialStore(sm, master_key=Fernet.generate_key())
    with pytest.raises(InvalidToken):
        await store_b.retrieve(cid)

    await store_a.delete(cid)


# ---------------------------------------------------------------------------
# PLAN_09 — credential pipeline (type column + bulk_retrieve)
# ---------------------------------------------------------------------------


async def test_store_with_type_persists_to_column(sm):
    from sqlalchemy import text as sql_text

    user = await _seed_user(sm)
    store = FernetCredentialStore(sm, master_key=Fernet.generate_key())
    cid = await store.store(
        user.id, "smtp-primary", {"user": "u", "password": "p"},
        credential_type="smtp",
    )
    async with sm() as s:
        row = await s.execute(
            sql_text("SELECT type FROM credentials WHERE id = :cid"),
            {"cid": cid},
        )
        assert row.scalar() == "smtp"

    await store.delete(cid)


async def test_store_default_type_is_unknown(sm):
    from sqlalchemy import text as sql_text

    user = await _seed_user(sm)
    store = FernetCredentialStore(sm, master_key=Fernet.generate_key())
    cid = await store.store(user.id, "legacy", {"k": "v"})
    async with sm() as s:
        row = await s.execute(
            sql_text("SELECT type FROM credentials WHERE id = :cid"),
            {"cid": cid},
        )
        assert row.scalar() == "unknown"

    await store.delete(cid)


async def test_store_rejects_invalid_type(sm):
    from sqlalchemy.exc import IntegrityError

    user = await _seed_user(sm)
    store = FernetCredentialStore(sm, master_key=Fernet.generate_key())
    with pytest.raises(IntegrityError):
        await store.store(
            user.id, "bad", {"v": 1}, credential_type="definitely_not_allowed",
        )


async def test_bulk_retrieve_happy(sm):
    user = await _seed_user(sm)
    store = FernetCredentialStore(sm, master_key=Fernet.generate_key())
    cid1 = await store.store(user.id, "a", {"v": 1}, credential_type="smtp")
    cid2 = await store.store(user.id, "b", {"v": 2}, credential_type="slack_webhook")
    cid3 = await store.store(user.id, "c", {"v": 3}, credential_type="http_bearer")

    try:
        got = await store.bulk_retrieve([cid1, cid2, cid3], owner_id=user.id)
        assert got == {cid1: {"v": 1}, cid2: {"v": 2}, cid3: {"v": 3}}
    finally:
        for cid in (cid1, cid2, cid3):
            await store.delete(cid)


async def test_bulk_retrieve_ownership_filter(sm):
    user_a = await _seed_user(sm)
    user_b = await _seed_user(sm)
    store = FernetCredentialStore(sm, master_key=Fernet.generate_key())
    cid_a = await store.store(user_a.id, "mine", {"v": "a"})

    try:
        with pytest.raises(KeyError, match="missing credential"):
            await store.bulk_retrieve([cid_a], owner_id=user_b.id)
    finally:
        await store.delete(cid_a)


async def test_bulk_retrieve_missing_id_raises(sm):
    from uuid import uuid4 as _uuid4

    user = await _seed_user(sm)
    store = FernetCredentialStore(sm, master_key=Fernet.generate_key())
    cid_real = await store.store(user.id, "real", {"v": 1})

    try:
        with pytest.raises(KeyError, match="missing credential"):
            await store.bulk_retrieve([cid_real, _uuid4()], owner_id=user.id)
    finally:
        await store.delete(cid_real)


async def test_bulk_retrieve_empty_list(sm):
    user = await _seed_user(sm)
    store = FernetCredentialStore(sm, master_key=Fernet.generate_key())
    got = await store.bulk_retrieve([], owner_id=user.id)
    assert got == {}


# ---------------------------------------------------------------------------
# PLAN_10 — list_by_owner (metadata-only, no plaintext exposure)
# ---------------------------------------------------------------------------


async def test_list_by_owner_happy(sm):
    import asyncio

    user = await _seed_user(sm)
    store = FernetCredentialStore(sm, master_key=Fernet.generate_key())
    # Store 3 credentials with a tiny sleep to guarantee created_at ordering.
    cid1 = await store.store(user.id, "first", {"a": 1}, credential_type="smtp")
    await asyncio.sleep(0.01)
    cid2 = await store.store(user.id, "second", {"b": 2}, credential_type="http_bearer")
    await asyncio.sleep(0.01)
    cid3 = await store.store(user.id, "third", {"c": 3}, credential_type="slack_webhook")

    try:
        rows = await store.list_by_owner(user.id)
        assert [r.id for r in rows] == [cid3, cid2, cid1]  # DESC by created_at
        assert [r.name for r in rows] == ["third", "second", "first"]
        assert [r.type for r in rows] == ["slack_webhook", "http_bearer", "smtp"]
        # Plaintext must not leak through DTO — dataclass has no such field.
        assert not any(hasattr(r, "plaintext") or hasattr(r, "encrypted_data") for r in rows)
    finally:
        for cid in (cid1, cid2, cid3):
            await store.delete(cid)


async def test_list_by_owner_empty(sm):
    user = await _seed_user(sm)
    store = FernetCredentialStore(sm, master_key=Fernet.generate_key())
    rows = await store.list_by_owner(user.id)
    assert rows == []


async def test_list_by_owner_ownership_filter(sm):
    user_a = await _seed_user(sm)
    user_b = await _seed_user(sm)
    store = FernetCredentialStore(sm, master_key=Fernet.generate_key())
    cid_a = await store.store(user_a.id, "a-only", {"v": 1})

    try:
        rows_b = await store.list_by_owner(user_b.id)
        assert rows_b == []
        rows_a = await store.list_by_owner(user_a.id)
        assert len(rows_a) == 1
        assert rows_a[0].id == cid_a
    finally:
        await store.delete(cid_a)
