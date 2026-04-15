"""Domain exception hierarchy with HTTP status carried on the class.

A single FastAPI exception handler in `app.main` maps every subclass to a
`JSONResponse(status_code=exc.http_status, content={"detail": exc.message})`,
so routers and services never build `HTTPException` by hand. This replaces
the per-router `_raise_http` helpers that duplicated error-code tables.

Each class documents the user-visible meaning because the status code alone
is ambiguous: 401 can mean "your login password was wrong" OR "your bearer
token is expired", and only the `AuthenticationError` variant needs the
`WWW-Authenticate: Bearer` header.
"""
from __future__ import annotations


class DomainError(Exception):
    """Base for all service-layer errors that map to HTTP responses."""

    http_status: int = 400
    headers: dict[str, str] | None = None

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


# ---------------------------------------------------------------------- auth


class AuthenticationError(DomainError):
    """401 — Bearer token missing, malformed, expired, or user not found.

    Carries `WWW-Authenticate: Bearer` so standards-compliant clients prompt
    for a fresh login.
    """

    http_status = 401
    headers = {"WWW-Authenticate": "Bearer"}


class InvalidCredentialsError(DomainError):
    """401 — wrong password on the login form.

    No `WWW-Authenticate` header: the client already knows to present
    credentials, the password was just wrong.
    """

    http_status = 401


class EmailNotVerifiedError(DomainError):
    """403 — login blocked because the user never clicked the verify link."""

    http_status = 403


class EmailExistsError(DomainError):
    """409 — registration email collision."""

    http_status = 409


class InvalidTokenError(DomainError):
    """400 — verify-email link token malformed, expired, or wrong purpose.

    Distinct from `AuthenticationError` because this is a user-visible "please
    request a fresh verification email" situation, not "please log in again".
    """

    http_status = 400


# ------------------------------------------------------------------ workflow


class NotFoundError(DomainError):
    """404 — resource missing, OR owned by a different user.

    Ownership failures return 404 (not 403) to avoid leaking existence of
    other users' resources. Matches PLAN_02 Q3 / ADR-015 §7 style.
    """

    http_status = 404


class QuotaExceededError(DomainError):
    """403 — workflow cap reached for the current plan tier."""

    http_status = 403


class InvalidGraphError(DomainError):
    """422 — DAG validation failed (cycle, unknown edge, duplicate id, empty)."""

    http_status = 422
