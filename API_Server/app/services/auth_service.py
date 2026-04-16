"""Auth business logic — bcrypt hashing, JWT issue/verify, registration flow.

The service owns all crypto/token decisions so routers stay thin HTTP
adapters. `UserRepository.get_password_hash` is the only path that touches
hash bytes; `_check_password` immediately hands the result to `bcrypt.checkpw`
and does not return it to callers.

Refactor: raises concrete `DomainError` subclasses (`EmailExistsError`,
`InvalidCredentialsError`, `EmailNotVerifiedError`, `InvalidTokenError`,
`AuthenticationError`) — the global exception handler in `app.main` maps
each to the correct HTTP status without per-router dispatch tables.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import bcrypt
import jwt
from auto_workflow_database.repositories.base import User, UserRepository

from app.config import Settings
from app.errors import (
    AuthenticationError,
    EmailExistsError,
    EmailNotVerifiedError,
    InvalidCredentialsError,
    InvalidTokenError,
)
from app.services.email_sender import EmailSender


class AuthService:
    def __init__(
        self,
        *,
        user_repo: UserRepository,
        email_sender: EmailSender,
        settings: Settings,
    ) -> None:
        self._users = user_repo
        self._email = email_sender
        self._s = settings

    # ------------------------------------------------------------- passwords

    def _hash_password(self, plaintext: str) -> bytes:
        return bcrypt.hashpw(
            plaintext.encode("utf-8"),
            bcrypt.gensalt(rounds=self._s.bcrypt_cost),
        )

    @staticmethod
    def _check_password(plaintext: str, hashed: bytes) -> bool:
        try:
            return bcrypt.checkpw(plaintext.encode("utf-8"), hashed)
        except ValueError:
            # Malformed hash in DB — treat as auth failure, never leak details.
            return False

    # ------------------------------------------------------------------ JWT

    def _issue_token(
        self, *, sub: UUID, purpose: str, ttl: timedelta, sub_prefix: str = "",
    ) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "sub": f"{sub_prefix}{sub}",
            "purpose": purpose,
            "iat": int(now.timestamp()),
            "exp": int((now + ttl).timestamp()),
        }
        return jwt.encode(payload, self._s.jwt_secret, algorithm=self._s.jwt_algorithm)

    def _decode_raw(self, token: str, *, expected_purpose: str) -> UUID:
        """Returns the `sub` UUID or raises `InvalidTokenError` (400).

        Callers that need a 401 (Bearer token auth failure) wrap the call and
        re-raise as `AuthenticationError` — see `decode_access_token`.
        """
        try:
            payload = jwt.decode(
                token, self._s.jwt_secret, algorithms=[self._s.jwt_algorithm]
            )
        except jwt.ExpiredSignatureError as e:
            raise InvalidTokenError("token expired") from e
        except jwt.InvalidTokenError as e:
            raise InvalidTokenError("invalid token") from e

        if payload.get("purpose") != expected_purpose:
            raise InvalidTokenError("token purpose mismatch")
        try:
            return UUID(payload["sub"])
        except (KeyError, ValueError) as e:
            raise InvalidTokenError("invalid subject") from e

    def issue_access_token(self, user_id: UUID) -> str:
        return self._issue_token(
            sub=user_id,
            purpose="access",
            ttl=timedelta(minutes=self._s.jwt_access_ttl_minutes),
        )

    def decode_access_token(self, token: str) -> UUID:
        """Bearer-token decode — `InvalidTokenError` is upgraded to `AuthenticationError`
        so `Depends(get_current_user)` callers return 401 + `WWW-Authenticate: Bearer`.
        """
        try:
            return self._decode_raw(token, expected_purpose="access")
        except InvalidTokenError as e:
            raise AuthenticationError(e.message) from e

    # --------------------------------------------------------- agent JWT

    def issue_agent_token(self, agent_id: UUID) -> str:
        return self._issue_token(
            sub=agent_id,
            purpose="agent",
            ttl=timedelta(hours=self._s.agent_jwt_ttl_hours),
            sub_prefix="agent:",
        )

    def decode_agent_token(self, token: str) -> UUID:
        try:
            payload = jwt.decode(
                token, self._s.jwt_secret, algorithms=[self._s.jwt_algorithm]
            )
        except jwt.InvalidTokenError:
            raise InvalidTokenError("invalid token")
        sub = payload.get("sub", "")
        if not sub.startswith("agent:") or payload.get("purpose") != "agent":
            raise InvalidTokenError("not an agent token")
        try:
            return UUID(sub.removeprefix("agent:"))
        except ValueError as e:
            raise InvalidTokenError("invalid agent subject") from e

    # --------------------------------------------------------------- flows

    async def register(self, *, email: str, password: str) -> User:
        existing = await self._users.get_by_email(email)
        if existing is not None:
            raise EmailExistsError("email already registered")

        user = await self._users.create(
            email=email,
            password_hash=self._hash_password(password),
        )

        verify_token = self._issue_token(
            sub=user.id,
            purpose="verify_email",
            ttl=timedelta(hours=self._s.jwt_verify_email_ttl_hours),
        )
        link = f"{self._s.app_base_url}/api/v1/auth/verify?token={verify_token}"
        await self._email.send_verification_email(email, link)
        return user

    async def verify_email(self, token: str) -> UUID:
        user_id = self._decode_raw(token, expected_purpose="verify_email")
        # mark_verified is idempotent — calling twice is safe and returns 200.
        await self._users.mark_verified(user_id)
        return user_id

    async def login(self, *, email: str, password: str) -> str:
        hashed = await self._users.get_password_hash(email)
        if hashed is None or not self._check_password(password, hashed):
            raise InvalidCredentialsError("invalid credentials")

        user = await self._users.get_by_email(email)
        if user is None:
            # Hash existed but row gone mid-flight. Treat as auth failure.
            raise InvalidCredentialsError("invalid credentials")
        if not user.is_verified:
            raise EmailNotVerifiedError("email not verified")

        return self.issue_access_token(user.id)

    async def current_user(self, access_token: str) -> User:
        user_id = self.decode_access_token(access_token)
        user = await self._users.get(user_id)
        if user is None:
            raise AuthenticationError("user not found")
        return user
