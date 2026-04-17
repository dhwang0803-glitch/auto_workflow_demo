"""Agent-side credential helpers — PLAN_10.

The Agent receives `credential_payloads` inside the `execute` WS message
(API_Server PLAN_08). Each payload is an ADR-013 hybrid envelope in
base64. This module unwraps them with the Agent's RSA private key and
exposes a minimal `CredentialStore` shim so `resolve_credential_refs`
(PLAN_08 worker-side code) can be reused verbatim on the Agent side.
"""
from __future__ import annotations

import base64
import json
from uuid import UUID

from auto_workflow_database.crypto.hybrid import hybrid_decrypt
from auto_workflow_database.repositories.base import (
    AgentCredentialPayload,
    CredentialStore,
)


def decrypt_payloads(
    payloads: list[dict], private_key_pem: bytes
) -> dict[UUID, dict]:
    """Each payload looks like::

        {"credential_id": "<uuid>",
         "wrapped_key":   "<b64>",
         "nonce":         "<b64>",
         "ciphertext":    "<b64>"}

    Returns plaintext dicts keyed by credential_id UUID. Any failure
    inside `hybrid_decrypt` propagates — caller maps to a generic
    execution failure (no credential_id leaked to logs).
    """
    out: dict[UUID, dict] = {}
    for p in payloads:
        envelope = AgentCredentialPayload(
            wrapped_key=base64.b64decode(p["wrapped_key"]),
            nonce=base64.b64decode(p["nonce"]),
            ciphertext=base64.b64decode(p["ciphertext"]),
        )
        plaintext = hybrid_decrypt(envelope, private_key_pem)
        out[UUID(p["credential_id"])] = json.loads(plaintext.decode("utf-8"))
    return out


class PreDecryptedCredentialStore(CredentialStore):
    """Read-only `CredentialStore` backed by an already-decrypted map.

    `bulk_retrieve` ignores `owner_id` — the server-side filter already
    ran when it called `retrieve_for_agent`, and the Agent has no DB
    access to re-check ownership anyway. Write methods raise because
    the Agent never persists credentials.
    """

    def __init__(self, decrypted: dict[UUID, dict]) -> None:
        self._decrypted = decrypted

    async def bulk_retrieve(
        self,
        credential_ids: list[UUID],
        *,
        owner_id: UUID,
    ) -> dict[UUID, dict]:
        if not credential_ids:
            return {}
        missing = [cid for cid in credential_ids if cid not in self._decrypted]
        if missing:
            raise KeyError("missing credential(s)")
        return {cid: self._decrypted[cid] for cid in credential_ids}

    async def store(self, *args, **kwargs) -> UUID:
        raise NotImplementedError("agent store is read-only")

    async def retrieve(self, credential_id: UUID) -> dict:
        raise NotImplementedError("use bulk_retrieve on the agent side")

    async def delete(self, credential_id: UUID) -> None:
        raise NotImplementedError("agent store is read-only")

    async def retrieve_for_agent(self, *args, **kwargs):
        raise NotImplementedError("agent does not re-wrap for itself")
