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
