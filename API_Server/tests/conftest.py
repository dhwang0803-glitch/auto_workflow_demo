"""Test fixtures for API_Server PLAN_01.

Builds a real FastAPI app against a live Postgres (set `DATABASE_URL`),
injects a `NoopEmailSender` so tests can assert on captured verification
links, and truncates `users` (CASCADE) between tests.
"""
from __future__ import annotations

import os
from typing import AsyncIterator

import pytest
import pytest_asyncio

sqlalchemy = pytest.importorskip("sqlalchemy")
asyncpg = pytest.importorskip("asyncpg")  # noqa: F841
cryptography = pytest.importorskip("cryptography")

from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.config import Settings
from app.main import create_app
from app.services.email_sender import NoopEmailSender

DATABASE_URL = os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set — API_Server tests require live Postgres",
)


def _make_settings(**overrides) -> Settings:
    # All env lookups are routed through Settings so tests never mutate os.environ.
    base = dict(
        database_url=DATABASE_URL or "",
        jwt_secret="test-secret-do-not-use-in-prod",
        jwt_algorithm="HS256",
        jwt_access_ttl_minutes=60,
        jwt_verify_email_ttl_hours=24,
        email_sender="console",
        app_base_url="http://testserver",
        password_min_length=8,
        bcrypt_cost=4,  # faster hashing for tests
        # Low quota so the rejection test doesn't need to spin up 100 rows.
        workflow_limit_light=3,
        workflow_limit_middle=5,
        workflow_limit_heavy=10,
        # Ephemeral Fernet key per test session — blueprint §1.6 invariant 3.
        credential_master_key=Fernet.generate_key().decode("utf-8"),
        # ADR-019 OAuth — values are non-empty so the container builds a
        # real GoogleOAuthClient. Individual tests swap its http_client
        # for an httpx.MockTransport, so no network I/O ever happens.
        google_oauth_client_id="test-client-id",
        google_oauth_client_secret="test-client-secret",
        google_oauth_redirect_uri="http://testserver/api/v1/oauth/google/callback",
    )
    base.update(overrides)
    return Settings(**base)


@pytest_asyncio.fixture
async def email_sender() -> NoopEmailSender:
    return NoopEmailSender()


@pytest_asyncio.fixture
async def app(email_sender):
    settings = _make_settings()
    fastapi_app = create_app(settings, email_sender=email_sender)
    # Start lifespan manually via the httpx transport below; nothing to do here.
    yield fastapi_app


@pytest_asyncio.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        # Trigger lifespan startup via first request-like setup.
        async with app.router.lifespan_context(app):
            await _truncate_users(app)
            yield c
            await _truncate_users(app)


async def _truncate_users(app) -> None:
    sm = app.state.sessionmaker
    async with sm() as s, s.begin():
        await s.execute(text("TRUNCATE users CASCADE"))


@pytest_asyncio.fixture
async def authed_client(client, email_sender):
    """Registered, verified, and logged-in AsyncClient.

    Sets the Authorization header on the underlying client so every
    subsequent request is authenticated. Returns the client *plus* the
    decoded user email so tests can cross-reference ownership.
    """
    email = "owner@example.com"
    password = "correct-horse-8"
    r = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )
    assert r.status_code == 201

    # Pull the verify-email link captured by NoopEmailSender.
    from urllib.parse import parse_qs, urlparse
    link = next(l for (to, l) in email_sender.sent if to == email)
    token = parse_qs(urlparse(link).query)["token"][0]
    v = await client.get("/api/v1/auth/verify", params={"token": token})
    assert v.status_code == 200

    login = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
    )
    assert login.status_code == 200
    access = login.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {access}"
    client.email = email  # type: ignore[attr-defined]
    return client
