"""Generic E2E runner for any GoogleWorkspaceNode — ADR-019 verification.

Dispatches via the node registry so the same script handles all 6
Workspace node types (gmail/drive/sheets/docs/slides/calendar). Replaces
the per-node one-offs that would otherwise duplicate the bootstrap.

Run via infra/scripts/run_e2e_workspace_node.sh which sets up
the Cloud SQL proxy + injects DATABASE_URL / CREDENTIAL_MASTER_KEY /
GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET from Secret Manager.

Args (env vars so nothing leaks through argv):
  CRED_ID      — UUID of the google_oauth credential row to use
  NODE_TYPE    — registered node type, e.g. google_drive_upload_file
  NODE_CONFIG  — JSON dict of node-specific config (credential_id is
                 injected from CRED_ID, do not duplicate)
"""
from __future__ import annotations

import asyncio
import json
import os
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from auto_workflow_database.repositories.credential_store import (
    FernetCredentialStore,
)

import src.nodes  # noqa: F401 — populates registry via self-registration
from src.nodes.google_workspace import GoogleWorkspaceNode
from src.nodes.registry import registry
from src.services.google_oauth_client import GoogleOAuthClient


async def main() -> None:
    db_url = os.environ["DATABASE_URL"]
    master_key = os.environ["CREDENTIAL_MASTER_KEY"].encode("utf-8")
    client_id = os.environ["GOOGLE_OAUTH_CLIENT_ID"]
    client_secret = os.environ["GOOGLE_OAUTH_CLIENT_SECRET"]

    cred_id = UUID(os.environ["CRED_ID"])
    node_type = os.environ["NODE_TYPE"]
    node_config = json.loads(os.environ["NODE_CONFIG"])
    node_config["credential_id"] = str(cred_id)

    engine = create_async_engine(db_url, pool_pre_ping=True)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    store = FernetCredentialStore(sm, master_key=master_key)

    async with httpx.AsyncClient(timeout=30.0) as http:
        oauth_client = GoogleOAuthClient(
            client_id=client_id,
            client_secret=client_secret,
            http_client=http,
        )
        GoogleWorkspaceNode.configure(
            credential_store=store,
            oauth_client=oauth_client,
            http_client=http,
        )

        node = registry.get(node_type)()
        result = await node.execute({}, node_config)
        print(f"{node_type.upper()}_RESULT:", result)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
