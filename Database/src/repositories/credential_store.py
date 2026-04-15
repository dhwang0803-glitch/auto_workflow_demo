"""Fernet credential store — ADR-004.

Plaintext credentials exist only inside `retrieve()` return values. They
MUST NOT be logged, echoed in API responses, or stored in any form other
than this table's `encrypted_data` column.
"""
from __future__ import annotations

import json
from uuid import UUID

from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker

from Database.src.models.extras import Credential as CredentialORM
from Database.src.repositories.base import CredentialStore


class FernetCredentialStore(CredentialStore):
    def __init__(
        self, sessionmaker: async_sessionmaker, *, master_key: bytes
    ) -> None:
        self._sm = sessionmaker
        self._f = Fernet(master_key)

    async def store(self, owner_id: UUID, name: str, plaintext: dict) -> UUID:
        blob = self._f.encrypt(json.dumps(plaintext).encode("utf-8"))
        async with self._sm() as s, s.begin():
            row = CredentialORM(
                owner_id=owner_id, name=name, encrypted_data=blob
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

    async def delete(self, credential_id: UUID) -> None:
        async with self._sm() as s, s.begin():
            row = await s.get(CredentialORM, credential_id)
            if row is not None:
                await s.delete(row)
