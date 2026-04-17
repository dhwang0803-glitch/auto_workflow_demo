"""Pydantic schemas for credential CRUD — PLAN_07.

`plaintext` appears only in `CredentialCreate` (request body). The response
model intentionally omits it so no code path can accidentally serialize the
secret back to the client. ADR-004 + blueprint §1.6 invariant.
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


CredentialType = Literal["smtp", "postgres_dsn", "slack_webhook", "http_bearer"]


class CredentialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: CredentialType
    plaintext: dict


class CredentialResponse(BaseModel):
    id: UUID
    name: str
    type: str
