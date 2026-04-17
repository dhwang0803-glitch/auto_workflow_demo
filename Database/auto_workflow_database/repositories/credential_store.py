"""Fernet credential store — ADR-004.

Plaintext credentials exist only inside `retrieve()` / `bulk_retrieve()`
return values. They MUST NOT be logged, echoed in API responses, or stored
in any form other than this table's `encrypted_data` column.
"""
from __future__ import annotations

import json
from uuid import UUID

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from auto_workflow_database.crypto.hybrid import hybrid_encrypt
from auto_workflow_database.models.extras import Credential as CredentialORM
from auto_workflow_database.repositories.base import AgentCredentialPayload, CredentialStore


class FernetCredentialStore(CredentialStore):
    def __init__(
        self, sessionmaker: async_sessionmaker, *, master_key: bytes
    ) -> None:
        self._sm = sessionmaker
        self._f = Fernet(master_key)

    async def store(
        self,
        owner_id: UUID,
        name: str,
        plaintext: dict,
        *,
        credential_type: str = "unknown",
    ) -> UUID:
        blob = self._f.encrypt(json.dumps(plaintext).encode("utf-8"))
        async with self._sm() as s, s.begin():
            row = CredentialORM(
                owner_id=owner_id,
                name=name,
                type=credential_type,
                encrypted_data=blob,
            )
            s.add(row)
            await s.flush()
            return row.id

    async def retrieve(self, credential_id: UUID) -> dict:
        async with self._sm() as s:
            row = await s.get(CredentialORM, credential_id)
            if row is None:
                raise KeyError(f"credential {credential_id} not found")
            # InvalidToken propagates on wrong key / tampered ciphertext —
            # caller must treat that as a security event.
            plaintext = self._f.decrypt(row.encrypted_data)
            return json.loads(plaintext.decode("utf-8"))

    async def bulk_retrieve(
        self,
        credential_ids: list[UUID],
        *,
        owner_id: UUID,
    ) -> dict[UUID, dict]:
        if not credential_ids:
            return {}
        async with self._sm() as s:
            stmt = select(CredentialORM).where(
                CredentialORM.owner_id == owner_id,
                CredentialORM.id.in_(credential_ids),
            )
            rows = (await s.execute(stmt)).scalars().all()
        found = {
            row.id: json.loads(self._f.decrypt(row.encrypted_data).decode("utf-8"))
            for row in rows
        }
        if len(found) != len(set(credential_ids)):
            # Intentionally generic — enumerating which ids belong to a
            # different owner would leak existence to a malicious caller.
            raise KeyError("missing credential(s)")
        return found

    async def delete(self, credential_id: UUID) -> None:
        async with self._sm() as s, s.begin():
            row = await s.get(CredentialORM, credential_id)
            if row is not None:
                await s.delete(row)

    async def retrieve_for_agent(
        self,
        credential_id: UUID,
        *,
        agent_public_key_pem: bytes,
    ) -> AgentCredentialPayload:
        plaintext_dict = await self.retrieve(credential_id)
        plaintext_bytes = json.dumps(plaintext_dict).encode("utf-8")
        return hybrid_encrypt(plaintext_bytes, agent_public_key_pem)
