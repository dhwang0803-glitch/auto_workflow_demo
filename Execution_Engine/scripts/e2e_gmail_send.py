"""One-off Gmail send against a live staging credential — ADR-019 verification.

Run via Database/deploy/scripts/run_e2e_gmail_send.sh which sets up the
Cloud SQL proxy + injects DATABASE_URL / CREDENTIAL_MASTER_KEY /
GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET from Secret Manager.

Args (via env vars so nothing leaks through argv):
  CRED_ID      — UUID of the google_oauth credential row to use
  TO_ADDR      — recipient
  SUBJECT      — mail subject
  BODY         — plaintext body
"""
from __future__ import annotations

import asyncio
import os
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from auto_workflow_database.repositories.credential_store import (
    FernetCredentialStore,
)
from src.nodes.gmail_send import GmailSendNode
from src.nodes.google_workspace import GoogleWorkspaceNode
from src.services.google_oauth_client import GoogleOAuthClient


async def main() -> None:
    db_url = os.environ["DATABASE_URL"]
    master_key = os.environ["CREDENTIAL_MASTER_KEY"].encode("utf-8")
    client_id = os.environ["GOOGLE_OAUTH_CLIENT_ID"]
    client_secret = os.environ["GOOGLE_OAUTH_CLIENT_SECRET"]

    cred_id = UUID(os.environ["CRED_ID"])
    to_addr = os.environ["TO_ADDR"]
    subject = os.environ["SUBJECT"]
    body = os.environ.get("BODY", "")

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

        node = GmailSendNode()
        result = await node.execute(
            {},
            {
                "credential_id": str(cred_id),
                "to": to_addr,
                "subject": subject,
                "body": body,
            },
        )
        print("GMAIL_SEND_RESULT:", result)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
