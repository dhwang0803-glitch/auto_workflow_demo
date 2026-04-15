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
from auto_workflow_database.repositories.user_repository import (
    PostgresUserRepository,
)

from fastapi import FastAPI

from app.config import Settings
from app.routers.auth import router as auth_router
from app.services.auth_service import AuthService
from app.services.email_sender import EmailSender, make_email_sender


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
        sender = email_sender or make_email_sender(s)

        app.state.settings = s
        app.state.engine = engine
        app.state.sessionmaker = sessionmaker
        app.state.user_repo = user_repo
        app.state.email_sender = sender
        app.state.auth_service = AuthService(
            user_repo=user_repo,
            email_sender=sender,
            settings=s,
        )
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="auto_workflow API", version="0.1.0", lifespan=lifespan)
    app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"service": "api_server", "version": "0.1.0"}

    return app


app = create_app()
