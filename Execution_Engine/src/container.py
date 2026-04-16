"""WorkerContainer — single place to create all Execution_Engine dependencies.

Production code calls WorkerContainer() once at process startup.
Tests inject InMemory fakes via constructor parameters instead.
"""
from __future__ import annotations

import os

from auto_workflow_database.repositories._session import build_engine, build_sessionmaker
from auto_workflow_database.repositories.base import ExecutionRepository, WorkflowRepository
from auto_workflow_database.repositories.execution_repository import PostgresExecutionRepository
from auto_workflow_database.repositories.workflow_repository import PostgresWorkflowRepository

from src.nodes.registry import NodeRegistry, registry as default_registry


class WorkerContainer:

    def __init__(
        self,
        *,
        exec_repo: ExecutionRepository | None = None,
        wf_repo: WorkflowRepository | None = None,
        node_registry: NodeRegistry | None = None,
    ) -> None:
        if exec_repo is not None and wf_repo is not None:
            # Test mode: use injected fakes, no DB connection
            self.exec_repo = exec_repo
            self.wf_repo = wf_repo
            self.node_registry = node_registry or default_registry
            self._engine = None
            return

        # Production mode: build from DATABASE_URL
        engine = build_engine(os.environ["DATABASE_URL"])
        sm = build_sessionmaker(engine)
        self._engine = engine
        self.exec_repo = PostgresExecutionRepository(sm)
        self.wf_repo = PostgresWorkflowRepository(sm)
        self.node_registry = node_registry or default_registry

    async def dispose(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
