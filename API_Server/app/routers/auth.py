"""Auth endpoints — register / verify / login / me / refresh.

Thin HTTP adapters around `AuthService`. Domain errors raised by the
service layer bubble up to the global `DomainError` exception handler
in `app.main`, so this module has no try/except or status-code tables.
"""
from __future__ import annotations

from auto_workflow_database.repositories.base import User

from fastapi import APIRouter, Depends, Query, status
from fastapi.security import OAuth2PasswordRequestForm

from app.dependencies import get_auth_service, get_current_user
from app.models.auth import (
    MessageResponse,
    TokenResponse,
    UserRegister,
    UserResponse,
    VerifyResponse,
)
from app.services.auth_service import AuthService

router = APIRouter()


@router.post(
    "/register",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    body: UserRegister,
    auth: AuthService = Depends(get_auth_service),
) -> MessageResponse:
    await auth.register(email=body.email, password=body.password)
    return MessageResponse(
        message="verification email sent — check your inbox to activate the account"
    )


@router.get("/verify", response_model=VerifyResponse)
async def verify(
    token: str = Query(..., min_length=10),
    auth: AuthService = Depends(get_auth_service),
) -> VerifyResponse:
    user_id = await auth.verify_email(token)
    return VerifyResponse(status="verified", user_id=user_id)


@router.post("/login", response_model=TokenResponse)
async def login(
    form: OAuth2PasswordRequestForm = Depends(),
    auth: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    token = await auth.login(email=form.username, password=form.password)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserResponse)
async def me(current: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse.model_validate(current)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    current: User = Depends(get_current_user),
    auth: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    return TokenResponse(access_token=auth.issue_access_token(current.id))
