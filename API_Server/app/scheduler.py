"""Scheduler worker — run as: python -m app.scheduler

Polls apscheduler_jobs in PostgreSQL and fires due jobs. Each fired job
calls run_scheduled_execution() which creates a queued execution row via
WorkflowService.execute_workflow(). The API process only writes jobs to
the table; this worker is the sole consumer.
"""
from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from auto_workflow_database.repositories._session import build_engine, build_sessionmaker
from auto_workflow_database.repositories.execution_repository import PostgresExecutionRepository
from auto_workflow_database.repositories.user_repository import PostgresUserRepository
from auto_workflow_database.repositories.workflow_repository import PostgresWorkflowRepository

from app.config import Settings
from app.services.workflow_service import WorkflowService

logger = logging.getLogger("app.scheduler")
_svc: WorkflowService | None = None
_user_repo: PostgresUserRepository | None = None


def run_scheduled_execution(workflow_id: str, owner_id: str) -> None:
    asyncio.get_event_loop().run_until_complete(
        _run_async(UUID(workflow_id), UUID(owner_id))
    )


async def _run_async(workflow_id: UUID, owner_id: UUID) -> None:
    user = await _user_repo.get(owner_id)
    if user is None:
        logger.error("scheduled exec skipped: user %s not found", owner_id)
        return
    try:
        ex = await _svc.execute_workflow(user, workflow_id)
        logger.info("scheduled exec created: %s for workflow %s", ex.id, workflow_id)
    except Exception:
        logger.exception("scheduled exec failed for workflow %s", workflow_id)


async def main() -> None:
    global _svc, _user_repo
    s = Settings()
    engine = build_engine(s.database_url)
    sm = build_sessionmaker(engine)
    _user_repo = PostgresUserRepository(sm)
    workflow_repo = PostgresWorkflowRepository(sm)
    execution_repo = PostgresExecutionRepository(sm)
    _svc = WorkflowService(
        repo=workflow_repo, execution_repo=execution_repo, settings=s
    )
    scheduler = AsyncIOScheduler(
        jobstores={"default": SQLAlchemyJobStore(url=s.scheduler_jobstore_url)},
    )
    scheduler.start()
    logger.info("scheduler worker started")
    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown()
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
