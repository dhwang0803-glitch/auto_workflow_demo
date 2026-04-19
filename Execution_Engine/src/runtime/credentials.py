"""Credential resolution — PLAN_08 blueprint §2 Update.

Walks a workflow graph, decrypts every referenced credential in one
`bulk_retrieve` call (scoped to the workflow's owner), and returns a
deep-copied graph with `credential_ref` keys replaced by the requested
plaintext fields. The input graph is never mutated — retries and
execution logs must not surface resolved plaintext.

Plaintext lifetime: this function's return value holds resolved config
values in memory. Callers (the Celery dispatcher) pass it directly to
`run_workflow`, which hands each node its slice and drops the rest on
scope exit. Never log / store the returned graph.
"""
from __future__ import annotations

import copy
from uuid import UUID

from auto_workflow_database.repositories.base import CredentialStore


def graph_has_credential_refs(graph: dict) -> bool:
    for node in graph.get("nodes", []):
        cfg = node.get("config") or {}
        if cfg.get("credential_ref"):
            return True
    return False


async def resolve_credential_refs(
    graph: dict,
    store: CredentialStore,
    owner_id: UUID,
) -> dict:
    ids: list[UUID] = []
    for node in graph.get("nodes", []):
        ref = (node.get("config") or {}).get("credential_ref")
        if ref and "credential_id" in ref:
            ids.append(UUID(ref["credential_id"]))
    if not ids:
        return graph

    decrypted = await store.bulk_retrieve(ids, owner_id=owner_id)

    resolved = copy.deepcopy(graph)
    for node in resolved.get("nodes", []):
        cfg = node.get("config") or {}
        ref = cfg.get("credential_ref")
        if not ref:
            continue
        cid = UUID(ref["credential_id"])
        plaintext = decrypted[cid]
        for src_key, dst_key in ref.get("inject", {}).items():
            # ADR-019 — OAuth nodes don't pull plaintext fields (refresh
            # is deferred to node execution), but still need the credential_id
            # to call store.retrieve() / _ensure_fresh_token. The "credential_id"
            # src key is the one escape hatch; all other keys must exist in
            # the decrypted dict or the bulk_retrieve-ownership check is moot.
            if src_key == "credential_id":
                cfg[dst_key] = str(cid)
            else:
                cfg[dst_key] = plaintext[src_key]
        cfg.pop("credential_ref", None)
    return resolved
