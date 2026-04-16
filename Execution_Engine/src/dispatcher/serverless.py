"""Celery dispatcher — enqueue + execute serverless workflows.

The Celery task is a thin sync wrapper around _execute(), which is the
testable async core. Tests call _execute() directly with InMemory fakes;
production wires Postgres repos via WorkerContainer.
"""
from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from celery import Celery

from auto_workflow_database.repositories.base import (
    ExecutionRepository,
    WorkflowRepository,
)

from src.container import WorkerContainer
from src.nodes.registry import NodeRegistry
from src.runtime.executor import run_workflow

logger = logging.getLogger(__name__)

celery_app = Celery("execution_engine")
celery_app.config_from_object("config.celery_config")

_container: WorkerContainer | None = None


def _ensure_container() -> WorkerContainer:
    global _container
    if _container is None:
        _container = WorkerContainer()
    return _container


@celery_app.task(name="execute_workflow", bind=True, max_retries=0)
def run_workflow_task(self, execution_id: str) -> None:
    c = _ensure_container()
    asyncio.run(_execute(
        execution_id,
        exec_repo=c.exec_repo,
        wf_repo=c.wf_repo,
        node_registry=c.node_registry,
    ))


async def _execute(
    execution_id: str,
    *,
    exec_repo: ExecutionRepository,
    wf_repo: WorkflowRepository,
    node_registry: NodeRegistry,
) -> None:
    eid = UUID(execution_id)
    execution = await exec_repo.get(eid)
    if execution is None:
        logger.error("execution %s not found, skipping", execution_id)
        return

    workflow = await wf_repo.get(execution.workflow_id)
    if workflow is None:
        logger.error(
            "workflow %s not found for execution %s",
            execution.workflow_id, execution_id,
        )
        await exec_repo.update_status(
            eid, "failed", error={"message": "workflow not found"},
        )
        return

    await run_workflow(workflow.graph, execution, exec_repo, node_registry)
