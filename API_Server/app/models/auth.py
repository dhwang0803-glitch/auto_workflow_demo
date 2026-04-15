"""Pydantic request/response schemas for the auth router.

These are the **external** API contract. They never carry `password_hash`
or any other internal field; the Database `User` DTO is mapped onto
`UserResponse` in the service layer.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    plan_tier: str
    is_verified: bool
    created_at: datetime | None = None


class VerifyResponse(BaseModel):
    status: str
    user_id: UUID


class MessageResponse(BaseModel):
    message: str
