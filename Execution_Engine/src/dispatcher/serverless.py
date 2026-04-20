"""Celery dispatcher — enqueue + execute serverless workflows.

The Celery task is a thin sync wrapper around _execute(), which is the
testable async core. Tests call _execute() directly with InMemory fakes;
production wires Postgres repos via WorkerContainer.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any
from uuid import UUID

from celery import Celery

from auto_workflow_database.repositories.base import (
    CredentialStore,
    ExecutionRepository,
    WorkflowRepository,
)

from src.container import WorkerContainer
from src.nodes.registry import NodeRegistry
from src.runtime.credentials import graph_has_credential_refs, resolve_credential_refs
from src.runtime.executor import run_workflow

logger = logging.getLogger(__name__)

celery_app = Celery("execution_engine")
celery_app.config_from_object("config.celery_config")


# ADR-021 §5 — SETNX idempotency. 24h TTL: workflow runtimes are measured
# in seconds; 24h is long enough to absorb any pathological Celery retry
# window and short enough that Redis memory stays bounded. Values:
# "running" while in-flight, "completed" after finalize.
IDEMPOTENCY_KEY_TEMPLATE = "execution:{execution_id}"
IDEMPOTENCY_TTL_SECONDS = 86400


def _make_redis_client() -> Any:
    """Build an async Redis client from CELERY_BROKER_URL.

    Factored out so tests can monkeypatch it to return fakeredis. decode_responses
    is True so SETNX return values + stored sentinels round-trip as str, not bytes.
    """
    import redis.asyncio as redis_async

    url = os.environ["CELERY_BROKER_URL"]
    return redis_async.from_url(url, decode_responses=True)


@celery_app.task(name="execute_workflow", bind=True, max_retries=0)
def run_workflow_task(self, execution_id: str) -> None:
    # Build a fresh container per task. A module-level cached container
    # holds an AsyncEngine whose connection pool is bound to the event
    # loop of the first asyncio.run() call; subsequent tasks get new
    # event loops and the pool's asyncpg transports go stale
    # ("'NoneType' object has no attribute 'send'"). The pool-setup
    # overhead is negligible compared to node execution time.
    asyncio.run(_run_task(execution_id))


async def _run_task(execution_id: str) -> None:
    # ADR-021 §5 — SETNX dedup guards against:
    #   1. Celery `task_acks_late=True` + worker crash → task redelivery
    #   2. API_Server double-enqueue on the same execution_id (e.g., a
    #      retry-on-timeout in the caller pattern)
    # execution_id is a UUID, so the "same id collides across users" case
    # can't happen. Lock release transitions the value to "completed"
    # rather than DEL so a late redeliver still sees the sentinel and
    # skips (instead of re-acquiring because the key expired between our
    # DEL and the redeliver).
    redis_client = _make_redis_client()
    key = IDEMPOTENCY_KEY_TEMPLATE.format(execution_id=execution_id)
    try:
        acquired = await redis_client.set(
            key, "running", nx=True, ex=IDEMPOTENCY_TTL_SECONDS,
        )
        if not acquired:
            logger.info(
                "execution %s already in-flight or completed, skipping duplicate",
                execution_id,
            )
            return

        c = WorkerContainer()
        try:
            await _execute(
                execution_id,
                exec_repo=c.exec_repo,
                wf_repo=c.wf_repo,
                node_registry=c.node_registry,
                credential_store=c.credential_store,
            )
        finally:
            await c.dispose()
            await redis_client.set(
                key, "completed", ex=IDEMPOTENCY_TTL_SECONDS,
            )
    finally:
        # redis.asyncio clients hold a connection pool that survives past
        # the event loop unless explicitly closed, leading to "Task was
        # destroyed but it is pending" warnings in the Celery log.
        await redis_client.aclose()


async def _execute(
    execution_id: str,
    *,
    exec_repo: ExecutionRepository,
    wf_repo: WorkflowRepository,
    node_registry: NodeRegistry,
    credential_store: CredentialStore | None = None,
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

    # PLAN_08 — resolve credential_refs before node execution so plaintext
    # stays inside this process. Runs once per execution (blueprint Q2).
    try:
        if credential_store is not None:
            graph = await resolve_credential_refs(
                workflow.graph, credential_store, workflow.owner_id
            )
        else:
            graph = workflow.graph
            if graph_has_credential_refs(graph):
                await exec_repo.update_status(
                    eid, "failed",
                    error={"message": "credential store not configured"},
                )
                return
    except KeyError:
        # Race between API_Server validation and Worker pickup (credential
        # was deleted in between). Generic message — no id leakage.
        await exec_repo.update_status(
            eid, "failed", error={"message": "credential resolution failed"},
        )
        return

    await run_workflow(graph, execution, exec_repo, node_registry)
