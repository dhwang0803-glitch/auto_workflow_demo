"""FastAPI application factory — wires Settings, DB engine, repositories,
and the auth service onto `app.state`. Dependency providers in
`app.dependencies` read from there.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from auto_workflow_database.repositories._session import (
    build_engine,
    build_sessionmaker,
)
from auto_workflow_database.repositories.execution_repository import (
    PostgresExecutionRepository,
)
from auto_workflow_database.repositories.user_repository import (
    PostgresUserRepository,
)
from auto_workflow_database.repositories.workflow_repository import (
    PostgresWorkflowRepository,
)

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError

from app.config import Settings
from app.errors import DomainError
from app.routers.auth import router as auth_router
from app.routers.executions import router as executions_router
from app.routers.workflows import router as workflows_router
from app.services.auth_service import AuthService
from app.services.email_sender import EmailSender, make_email_sender
from app.services.workflow_service import WorkflowService


def create_app(
    settings: Settings | None = None,
    *,
    email_sender: EmailSender | None = None,
) -> FastAPI:
    s = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = build_engine(s.database_url)
        sessionmaker = build_sessionmaker(engine)
        user_repo = PostgresUserRepository(sessionmaker)
        workflow_repo = PostgresWorkflowRepository(sessionmaker)
        execution_repo = PostgresExecutionRepository(sessionmaker)
        sender = email_sender or make_email_sender(s)

        app.state.settings = s
        app.state.engine = engine
        app.state.sessionmaker = sessionmaker
        app.state.user_repo = user_repo
        app.state.workflow_repo = workflow_repo
        app.state.execution_repo = execution_repo
        app.state.email_sender = sender
        app.state.auth_service = AuthService(
            user_repo=user_repo,
            email_sender=sender,
            settings=s,
        )
        app.state.workflow_service = WorkflowService(
            repo=workflow_repo,
            execution_repo=execution_repo,
            settings=s,
        )
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="auto_workflow API", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(DomainError)
    async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        # Single mapping site for every service-layer error — the HTTP
        # status and (optional) headers live on the exception class itself,
        # so new error types only need one class definition.
        return JSONResponse(
            status_code=exc.http_status,
            content={"detail": exc.message},
            headers=exc.headers,
        )

    @app.exception_handler(DBAPIError)
    async def handle_db_error(request: Request, exc: DBAPIError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"error": "database_unavailable"},
        )

    app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
    app.include_router(
        workflows_router, prefix="/api/v1/workflows", tags=["workflows"]
    )
    app.include_router(
        executions_router, prefix="/api/v1/executions", tags=["executions"]
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"service": "api_server", "version": "0.1.0"}

    return app


app = create_app()
