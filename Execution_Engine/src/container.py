"""WorkerContainer — single place to create all Execution_Engine dependencies.

Production code calls WorkerContainer() once at process startup.
Tests inject InMemory fakes via constructor parameters instead.
"""
from __future__ import annotations

import os

import httpx

from auto_workflow_database.repositories._session import build_engine, build_sessionmaker
from auto_workflow_database.repositories.base import CredentialStore, ExecutionRepository, WorkflowRepository
from auto_workflow_database.repositories.credential_store import FernetCredentialStore
from auto_workflow_database.repositories.execution_repository import PostgresExecutionRepository
from auto_workflow_database.repositories.workflow_repository import PostgresWorkflowRepository

from src.nodes.google_workspace import GoogleWorkspaceNode
from src.nodes.registry import NodeRegistry, registry as default_registry
from src.services.google_oauth_client import GoogleOAuthClient


class WorkerContainer:

    def __init__(
        self,
        *,
        exec_repo: ExecutionRepository | None = None,
        wf_repo: WorkflowRepository | None = None,
        node_registry: NodeRegistry | None = None,
        credential_store: CredentialStore | None = None,
    ) -> None:
        self._http_client: httpx.AsyncClient | None = None
        if exec_repo is not None and wf_repo is not None:
            # Test mode: use injected fakes, no DB connection
            self.exec_repo = exec_repo
            self.wf_repo = wf_repo
            self.node_registry = node_registry or default_registry
            self.credential_store = credential_store
            self._engine = None
            return

        # Production mode: build from DATABASE_URL
        engine = build_engine(os.environ["DATABASE_URL"])
        sm = build_sessionmaker(engine)
        self._engine = engine
        self.exec_repo = PostgresExecutionRepository(sm)
        self.wf_repo = PostgresWorkflowRepository(sm)
        self.node_registry = node_registry or default_registry
        # Missing key means dev Worker without credential support — dispatcher
        # fails cleanly when a graph references a credential_ref.
        master_key = os.environ.get("CREDENTIAL_MASTER_KEY", "").encode("utf-8")
        self.credential_store = (
            FernetCredentialStore(sm, master_key=master_key) if master_key else None
        )

        # ADR-019 — Google Workspace nodes need a refresh client wired in
        # before the first execute() runs. A Worker without Google secrets
        # simply won't run Google nodes; configure() stays un-called and
        # GoogleWorkspaceNode._ensure_fresh_token raises a clear error.
        client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
        client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
        if client_id and client_secret and self.credential_store is not None:
            self._http_client = httpx.AsyncClient(timeout=30.0)
            GoogleWorkspaceNode.configure(
                credential_store=self.credential_store,
                oauth_client=GoogleOAuthClient(
                    client_id=client_id,
                    client_secret=client_secret,
                    http_client=self._http_client,
                ),
                http_client=self._http_client,
            )

    async def dispose(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
        if self._engine is not None:
            await self._engine.dispose()
