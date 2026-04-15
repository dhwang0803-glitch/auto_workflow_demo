"""UserRepository integration tests — groundwork for API_Server PLAN_01.

Covers the two isolation rules that the repository exists to enforce:
  1. `password_hash` is never on the `User` DTO (only `get_password_hash`)
  2. `is_verified` defaults to False for fresh signups, flips via
     `mark_verified`, and is idempotent on re-flip.

Postgres path requires `DATABASE_URL`; the InMemory double runs always.
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
asyncpg = pytest.importorskip("asyncpg")  # noqa: F841

from auto_workflow_database.repositories._session import (
    build_engine,
    build_sessionmaker,
)
from auto_workflow_database.repositories.user_repository import (
    PostgresUserRepository,
)

DATABASE_URL = os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set — UserRepository integration requires live DB",
)


@pytest.fixture
async def pg_repo():
    engine = build_engine(DATABASE_URL)
    sm = build_sessionmaker(engine)
    repo = PostgresUserRepository(sm)
    try:
        yield repo
    finally:
        # Clean up test rows. ON DELETE CASCADE from workflows/executions handles
        # downstream, but for this suite we only insert users so a direct
        # targeted delete by email prefix is enough.
        async with sm() as s, s.begin():
            from sqlalchemy import text as sql
            await s.execute(
                sql("DELETE FROM users WHERE email LIKE 'planauth-%'")
            )
        await engine.dispose()


async def test_create_user_defaults_to_unverified(pg_repo):
    email = f"planauth-{uuid4()}@test.local"
    user = await pg_repo.create(
        email=email, password_hash=b"hashed-bytes-abc", plan_tier="light"
    )
    assert user.email == email
    assert user.plan_tier == "light"
    assert user.is_verified is False
    assert not hasattr(user, "password_hash")


async def test_get_password_hash_returns_bytes_and_none_for_unknown(pg_repo):
    email = f"planauth-{uuid4()}@test.local"
    await pg_repo.create(
        email=email, password_hash=b"secret-bcrypt-hash", plan_tier="middle"
    )
    assert await pg_repo.get_password_hash(email) == b"secret-bcrypt-hash"
    assert await pg_repo.get_password_hash("nonexistent@test.local") is None


async def test_mark_verified_flips_flag_and_is_idempotent(pg_repo):
    user = await pg_repo.create(
        email=f"planauth-{uuid4()}@test.local",
        password_hash=b"x",
    )
    await pg_repo.mark_verified(user.id)
    after = await pg_repo.get(user.id)
    assert after is not None and after.is_verified is True

    # Second call must not error or change state.
    await pg_repo.mark_verified(user.id)
    again = await pg_repo.get(user.id)
    assert again.is_verified is True


async def test_get_by_email_case_insensitive(pg_repo):
    # users.email is CITEXT so lookups by mixed case must hit the row.
    email = f"planauth-{uuid4()}@test.local"
    created = await pg_repo.create(email=email, password_hash=b"h")
    upper = email.upper()
    found = await pg_repo.get_by_email(upper)
    assert found is not None and found.id == created.id


