"""FastAPI application factory — wires dependencies via AppContainer
and mounts them onto `app.state`.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError

from app.config import Settings
from app.container import AppContainer
from app.errors import DomainError
from app.routers.agents import router as agents_router
from app.routers.ai_composer import router as ai_composer_router
from app.routers.auth import router as auth_router
from app.routers.credentials import router as credentials_router
from app.routers.executions import router as executions_router
from app.routers.node_catalog import router as node_catalog_router
from app.routers.oauth_google import router as oauth_google_router
from app.routers.skills import router as skills_router
from app.routers.webhooks import router as webhooks_router
from app.routers.workflows import router as workflows_router
from app.services.ai_composer_service import LLMBackend
from app.services.email_sender import EmailSender


def create_app(
    settings: Settings | None = None,
    *,
    email_sender: EmailSender | None = None,
    ai_composer_backend: LLMBackend | None = None,
) -> FastAPI:
    # uvicorn installs its own handlers only on `uvicorn.*` loggers — our
    # `api_server.*` loggers inherit from root, which has no handler by
    # default under gunicorn/uvicorn workers. Without this, structured log
    # lines from EMAIL_SENDER=console etc. silently vanish in Cloud Run.
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=os.getenv("LOG_LEVEL", "INFO"),
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
    s = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        c = AppContainer(
            s,
            email_sender=email_sender,
            ai_composer_backend=ai_composer_backend,
        )
        app.state.settings = c.settings
        app.state.engine = c.engine
        app.state.sessionmaker = c.sessionmaker
        app.state.user_repo = c.user_repo
        app.state.workflow_repo = c.workflow_repo
        app.state.execution_repo = c.execution_repo
        app.state.email_sender = c.email_sender
        app.state.scheduler = c.scheduler
        app.state.auth_service = c.auth_service
        app.state.webhook_registry = c.webhook_registry
        app.state.agent_repo = c.agent_repo
        app.state.agent_connections = c.agent_connections
        app.state.credential_store = c.credential_store
        app.state.credential_service = c.credential_service
        app.state.oauth_state_signer = c.oauth_state_signer
        app.state.google_oauth_client = c.google_oauth_client
        app.state.workflow_service = c.workflow_service
        app.state.ai_composer_service = c.ai_composer_service
        app.state.skill_repo = c.skill_repo
        app.state.skill_bootstrap_service = c.skill_bootstrap_service
        try:
            yield
        finally:
            await c.dispose()

    app = FastAPI(title="auto_workflow API", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(DomainError)
    async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
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
    app.include_router(
        webhooks_router, prefix="/webhooks", tags=["webhooks"]
    )
    app.include_router(
        agents_router, prefix="/api/v1/agents", tags=["agents"]
    )
    app.include_router(
        credentials_router, prefix="/api/v1/credentials", tags=["credentials"]
    )
    app.include_router(
        oauth_google_router, prefix="/api/v1/oauth/google", tags=["oauth"]
    )
    app.include_router(
        node_catalog_router, prefix="/api/v1/nodes/catalog", tags=["nodes"]
    )
    app.include_router(
        ai_composer_router, prefix="/api/v1/ai", tags=["ai-composer"]
    )
    app.include_router(
        skills_router, prefix="/api/v1/skills", tags=["skills"]
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"service": "api_server", "version": "0.1.0"}

    return app


app = create_app()
