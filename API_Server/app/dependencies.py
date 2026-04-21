"""FastAPI dependency providers.

Everything reads from `request.app.state`, which is populated by the
`create_app` lifespan handler. Tests can override any of these through
`app.dependency_overrides` without touching module globals.
"""
from __future__ import annotations

from auto_workflow_database.repositories.base import User

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer

from app.config import Settings
from app.services.ai_composer_service import AIComposerService
from app.services.auth_service import AuthService

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


def get_ai_composer_service(request: Request) -> AIComposerService:
    return request.app.state.ai_composer_service


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    auth: AuthService = Depends(get_auth_service),
) -> User:
    # AuthenticationError from auth_service bubbles up to the global
    # DomainError handler in app.main, which emits 401 + WWW-Authenticate.
    return await auth.current_user(token)
