"""AppContainer — single place to create all API_Server dependencies.

main.py lifespan and scheduler.py both use this class instead of
assembling engine/sessionmaker/repos/services inline. Adding a new
repo or service means editing this one file.
"""
from __future__ import annotations

from auto_workflow_database.repositories._session import build_engine, build_sessionmaker
from auto_workflow_database.repositories.agent_repository import PostgresAgentRepository
from auto_workflow_database.repositories.execution_repository import PostgresExecutionRepository
from auto_workflow_database.repositories.user_repository import PostgresUserRepository
from auto_workflow_database.repositories.webhook_registry import PostgresWebhookRegistry
from auto_workflow_database.repositories.workflow_repository import PostgresWorkflowRepository

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import Settings
from app.services.auth_service import AuthService
from app.services.email_sender import EmailSender, make_email_sender
from app.services.workflow_service import WorkflowService


class AppContainer:

    def __init__(
        self,
        settings: Settings,
        *,
        email_sender: EmailSender | None = None,
    ) -> None:
        self.settings = settings
        self.engine = build_engine(settings.database_url)
        self.sessionmaker = build_sessionmaker(self.engine)

        self.user_repo = PostgresUserRepository(self.sessionmaker)
        self.workflow_repo = PostgresWorkflowRepository(self.sessionmaker)
        self.execution_repo = PostgresExecutionRepository(self.sessionmaker)
        self.webhook_registry = PostgresWebhookRegistry(self.sessionmaker)
        self.agent_repo = PostgresAgentRepository(self.sessionmaker)

        self.email_sender = email_sender or make_email_sender(settings)
        self.scheduler = AsyncIOScheduler(
            jobstores={"default": SQLAlchemyJobStore(url=settings.scheduler_jobstore_url)},
        )

        self.agent_connections: dict = {}

        self.auth_service = AuthService(
            user_repo=self.user_repo,
            email_sender=self.email_sender,
            settings=settings,
        )
        self.workflow_service = WorkflowService(
            repo=self.workflow_repo,
            execution_repo=self.execution_repo,
            settings=settings,
            scheduler=self.scheduler,
            webhook_registry=self.webhook_registry,
            user_repo=self.user_repo,
            agent_repo=self.agent_repo,
            agent_connections=self.agent_connections,
        )

    async def dispose(self) -> None:
        await self.engine.dispose()
