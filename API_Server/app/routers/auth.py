"""Auth endpoints — register / verify / login / me / refresh.

Thin HTTP adapters around `AuthService`. Domain errors (`AuthError`) get
mapped to HTTP status codes here; the service never knows about HTTP.
"""
from __future__ import annotations

from auto_workflow_database.repositories.base import User

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import OAuth2PasswordRequestForm

from app.dependencies import get_auth_service, get_current_user
from app.models.auth import (
    MessageResponse,
    TokenResponse,
    UserRegister,
    UserResponse,
    VerifyResponse,
)
from app.services.auth_service import AuthError, AuthService

router = APIRouter()


_ERROR_STATUS = {
    "email_exists": status.HTTP_409_CONFLICT,
    "invalid_credentials": status.HTTP_401_UNAUTHORIZED,
    "email_not_verified": status.HTTP_403_FORBIDDEN,
    "token_expired": status.HTTP_400_BAD_REQUEST,
    "token_invalid": status.HTTP_400_BAD_REQUEST,
    "token_wrong_purpose": status.HTTP_400_BAD_REQUEST,
}


def _raise_http(e: AuthError) -> None:
    code = _ERROR_STATUS.get(e.code, status.HTTP_400_BAD_REQUEST)
    raise HTTPException(status_code=code, detail=e.message)


@router.post(
    "/register",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    body: UserRegister,
    auth: AuthService = Depends(get_auth_service),
) -> MessageResponse:
    try:
        await auth.register(email=body.email, password=body.password)
    except AuthError as e:
        _raise_http(e)
    return MessageResponse(
        message="verification email sent — check your inbox to activate the account"
    )


@router.get("/verify", response_model=VerifyResponse)
async def verify(
    token: str = Query(..., min_length=10),
    auth: AuthService = Depends(get_auth_service),
) -> VerifyResponse:
    try:
        user_id = await auth.verify_email(token)
    except AuthError as e:
        _raise_http(e)
    return VerifyResponse(status="verified", user_id=user_id)


@router.post("/login", response_model=TokenResponse)
async def login(
    form: OAuth2PasswordRequestForm = Depends(),
    auth: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    try:
        token = await auth.login(email=form.username, password=form.password)
    except AuthError as e:
        _raise_http(e)
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
