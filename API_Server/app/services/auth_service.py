"""Auth business logic — bcrypt hashing, JWT issue/verify, registration flow.

The service owns all crypto/token decisions so routers stay thin HTTP
adapters. `UserRepository.get_password_hash` is the only path that touches
hash bytes; `verify_password` immediately hands the result to `bcrypt.checkpw`
and does not return it to callers.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import bcrypt
import jwt
from auto_workflow_database.repositories.base import User, UserRepository

from app.config import Settings
from app.services.email_sender import EmailSender


class AuthError(Exception):
    """Domain error — routers map these to HTTP status codes."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


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

    def _issue_token(self, *, sub: UUID, purpose: str, ttl: timedelta) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(sub),
            "purpose": purpose,
            "iat": int(now.timestamp()),
            "exp": int((now + ttl).timestamp()),
        }
        return jwt.encode(payload, self._s.jwt_secret, algorithm=self._s.jwt_algorithm)

    def _decode_token(self, token: str, *, expected_purpose: str) -> UUID:
        try:
            payload = jwt.decode(
                token, self._s.jwt_secret, algorithms=[self._s.jwt_algorithm]
            )
        except jwt.ExpiredSignatureError as e:
            raise AuthError("token_expired", "token expired") from e
        except jwt.InvalidTokenError as e:
            raise AuthError("token_invalid", "invalid token") from e

        if payload.get("purpose") != expected_purpose:
            raise AuthError("token_wrong_purpose", "token purpose mismatch")
        try:
            return UUID(payload["sub"])
        except (KeyError, ValueError) as e:
            raise AuthError("token_invalid", "invalid subject") from e

    def issue_access_token(self, user_id: UUID) -> str:
        return self._issue_token(
            sub=user_id,
            purpose="access",
            ttl=timedelta(minutes=self._s.jwt_access_ttl_minutes),
        )

    def decode_access_token(self, token: str) -> UUID:
        return self._decode_token(token, expected_purpose="access")

    # --------------------------------------------------------------- flows

    async def register(self, *, email: str, password: str) -> User:
        existing = await self._users.get_by_email(email)
        if existing is not None:
            raise AuthError("email_exists", "email already registered")

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
        user_id = self._decode_token(token, expected_purpose="verify_email")
        # mark_verified is idempotent — calling twice is safe and returns 200.
        await self._users.mark_verified(user_id)
        return user_id

    async def login(self, *, email: str, password: str) -> str:
        hashed = await self._users.get_password_hash(email)
        if hashed is None or not self._check_password(password, hashed):
            raise AuthError("invalid_credentials", "invalid credentials")

        user = await self._users.get_by_email(email)
        if user is None:
            # Shouldn't happen — hash existed but row gone. Treat as auth fail.
            raise AuthError("invalid_credentials", "invalid credentials")
        if not user.is_verified:
            raise AuthError("email_not_verified", "email not verified")

        return self.issue_access_token(user.id)

    async def current_user(self, access_token: str) -> User:
        user_id = self.decode_access_token(access_token)
        user = await self._users.get(user_id)
        if user is None:
            raise AuthError("token_invalid", "user not found")
        return user
