"""CredentialService — PLAN_07 BYO credential CRUD + validation.

Bridges the HTTP router to `CredentialStore`. Holds no plaintext state;
every decrypted value stays inside the caller's await scope.
"""
from __future__ import annotations

from uuid import UUID

from auto_workflow_database.repositories.base import CredentialStore, User
from sqlalchemy.exc import IntegrityError

from app.errors import DuplicateNameError, NotFoundError
from app.models.credential import CredentialCreate


class CredentialService:
    def __init__(self, *, store: CredentialStore) -> None:
        self._store = store

    async def create(self, user: User, body: CredentialCreate) -> UUID:
        try:
            return await self._store.store(
                user.id,
                body.name,
                body.plaintext,
                credential_type=body.type,
            )
        except IntegrityError as e:
            # credentials_owner_name_uq — "same user can't have two creds
            # with the same human-readable name."
            raise DuplicateNameError("credential name already used") from e

    async def delete(self, user: User, credential_id: UUID) -> None:
        # bulk_retrieve double-acts as ownership probe: it raises KeyError
        # when the id is missing OR owned by a different user. The decrypted
        # plaintext it returns is dropped immediately on scope exit.
        try:
            await self._store.bulk_retrieve([credential_id], owner_id=user.id)
        except KeyError:
            raise NotFoundError("credential not found")
        await self._store.delete(credential_id)

    async def validate_refs(
        self, user: User, credential_ids: list[UUID]
    ) -> None:
        """Called by `workflow_service.execute_workflow` to verify that every
        `credential_ref.credential_id` in the graph both exists AND is owned
        by `user`, before an execution row is created. Plaintext is dropped
        immediately — the Worker will re-resolve at node invocation time
        (Execution_Engine PLAN_08)."""
        if not credential_ids:
            return
        try:
            await self._store.bulk_retrieve(credential_ids, owner_id=user.id)
        except KeyError:
            raise NotFoundError("credential not found")
