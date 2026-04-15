"""Postgres user repository — API_Server PLAN_01 (auth + user management).

Owns the `users.password_hash` / `users.is_verified` columns added by
`migrations/20260416_add_user_auth_fields.sql`. Keeps `password_hash` out
of the `User` DTO so that hash bytes never travel through any endpoint
that serializes a user profile.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from auto_workflow_database.models.core import User as UserORM
from auto_workflow_database.repositories.base import (
    PlanTier,
    User,
    UserRepository,
)


def _to_dto(row: UserORM) -> User:
    return User(
        id=row.id,
        email=row.email,
        plan_tier=row.plan_tier,
        default_execution_mode=row.default_execution_mode,
        external_api_policy=dict(row.external_api_policy or {}),
        is_verified=row.is_verified,
        created_at=row.created_at,
    )


class PostgresUserRepository(UserRepository):
    def __init__(self, sessionmaker: async_sessionmaker) -> None:
        self._sm = sessionmaker

    async def create(
        self,
        *,
        email: str,
        password_hash: bytes,
        plan_tier: PlanTier = "light",
    ) -> User:
        async with self._sm() as s, s.begin():
            row = UserORM(
                email=email,
                plan_tier=plan_tier,
                password_hash=password_hash,
                is_verified=False,
            )
            s.add(row)
            await s.flush()
            return _to_dto(row)

    async def get(self, user_id: UUID) -> User | None:
        async with self._sm() as s:
            row = await s.get(UserORM, user_id)
            return _to_dto(row) if row else None

    async def get_by_email(self, email: str) -> User | None:
        async with self._sm() as s:
            stmt = select(UserORM).where(UserORM.email == email)
            row = (await s.execute(stmt)).scalar_one_or_none()
            return _to_dto(row) if row else None

    async def get_password_hash(self, email: str) -> bytes | None:
        async with self._sm() as s:
            stmt = select(UserORM.password_hash).where(UserORM.email == email)
            return (await s.execute(stmt)).scalar_one_or_none()

    async def mark_verified(self, user_id: UUID) -> None:
        async with self._sm() as s, s.begin():
            await s.execute(
                update(UserORM)
                .where(UserORM.id == user_id)
                .values(is_verified=True)
            )
