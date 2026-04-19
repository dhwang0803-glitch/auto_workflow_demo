"""ADR-019 — /authorize, /callback, /credentials/{id}/reauth integration.

Runs against the live Postgres mounted by conftest. The Google token
endpoint is intercepted with `httpx.MockTransport` so we exercise every
branch (code exchange, invalid_grant, duplicate name, reauth) without
any outbound network I/O.
"""
from __future__ import annotations

import os
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import pytest_asyncio

sqlalchemy = pytest.importorskip("sqlalchemy")
asyncpg = pytest.importorskip("asyncpg")  # noqa: F841

from app.services.google_oauth_client import GoogleOAuthClient

DATABASE_URL = os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set — requires live Postgres",
)


def _make_mock_client(handler) -> GoogleOAuthClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return GoogleOAuthClient(
        client_id="test-client-id",
        client_secret="test-client-secret",
        redirect_uri="http://testserver/api/v1/oauth/google/callback",
        http_client=http,
    )


def _ok_token_handler(*, with_refresh: bool = True):
    def handler(_req):
        body = {
            "access_token": "at-xxx",
            "expires_in": 3599,
            "scope": "https://www.googleapis.com/auth/gmail.send",
            "token_type": "Bearer",
        }
        if with_refresh:
            body["refresh_token"] = "rt-yyy"
        return httpx.Response(200, json=body)
    return handler


def _invalid_grant_handler(_req):
    return httpx.Response(
        400,
        json={
            "error": "invalid_grant",
            "error_description": "Token revoked",
        },
    )


# ---------------------------------------------------------------- /authorize


async def test_authorize_returns_google_consent_url(authed_client):
    r = await authed_client.post(
        "/api/v1/oauth/google/authorize",
        json={
            "credential_name": "work gmail",
            "scopes": ["https://www.googleapis.com/auth/gmail.send"],
        },
    )
    assert r.status_code == 200
    url = r.json()["authorize_url"]
    parsed = urlparse(url)
    assert parsed.netloc == "accounts.google.com"
    q = parse_qs(parsed.query)
    assert q["client_id"] == ["test-client-id"]
    assert q["response_type"] == ["code"]
    assert q["access_type"] == ["offline"]
    assert q["prompt"] == ["consent"]
    assert q["include_granted_scopes"] == ["true"]
    # State must be signed (has the body.sig structure).
    assert "." in q["state"][0]


async def test_authorize_requires_auth(client):
    r = await client.post(
        "/api/v1/oauth/google/authorize",
        json={"credential_name": "x", "scopes": ["scope"]},
    )
    assert r.status_code == 401


# ----------------------------------------------------------------- /callback


async def test_callback_creates_credential_on_success(authed_client):
    authed_client._transport.app.state.google_oauth_client = _make_mock_client(
        _ok_token_handler()
    )

    # Get a valid state by hitting /authorize first.
    auth = await authed_client.post(
        "/api/v1/oauth/google/authorize",
        json={
            "credential_name": "gmail-prod",
            "scopes": ["https://www.googleapis.com/auth/gmail.send"],
        },
    )
    state = parse_qs(urlparse(auth.json()["authorize_url"]).query)["state"][0]

    # Callback — httpx client follows redirects; disable so we see the 307.
    r = await authed_client.get(
        "/api/v1/oauth/google/callback",
        params={"code": "auth-code-xyz", "state": state},
        follow_redirects=False,
    )
    assert r.status_code in (302, 307)
    loc = r.headers["location"]
    assert "oauth=success" in loc
    assert "credential_id=" in loc

    # Credential landed in the DB with type=google_oauth + metadata.
    listing = await authed_client.get("/api/v1/credentials")
    assert listing.status_code == 200
    rows = listing.json()
    assert len(rows) == 1
    assert rows[0]["name"] == "gmail-prod"
    assert rows[0]["type"] == "google_oauth"


async def test_callback_with_user_denied_redirects_error(authed_client):
    r = await authed_client.get(
        "/api/v1/oauth/google/callback",
        params={"error": "access_denied"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 307)
    assert "oauth=error" in r.headers["location"]
    assert "reason=access_denied" in r.headers["location"]


async def test_callback_with_invalid_state_returns_400(authed_client):
    r = await authed_client.get(
        "/api/v1/oauth/google/callback",
        params={"code": "x", "state": "garbage.garbage"},
        follow_redirects=False,
    )
    assert r.status_code == 400


async def test_callback_missing_code_and_state_400(authed_client):
    r = await authed_client.get(
        "/api/v1/oauth/google/callback", follow_redirects=False
    )
    assert r.status_code == 400


async def test_callback_invalid_grant_redirects_with_reason(authed_client):
    authed_client._transport.app.state.google_oauth_client = _make_mock_client(
        _invalid_grant_handler
    )
    auth = await authed_client.post(
        "/api/v1/oauth/google/authorize",
        json={"credential_name": "gm", "scopes": ["scope"]},
    )
    state = parse_qs(urlparse(auth.json()["authorize_url"]).query)["state"][0]

    r = await authed_client.get(
        "/api/v1/oauth/google/callback",
        params={"code": "bad-code", "state": state},
        follow_redirects=False,
    )
    assert r.status_code in (302, 307)
    assert "reason=invalid_grant" in r.headers["location"]


async def test_callback_duplicate_name_redirects_error(authed_client):
    authed_client._transport.app.state.google_oauth_client = _make_mock_client(
        _ok_token_handler()
    )
    # Seed an existing credential with the same name so store raises IntegrityError.
    await authed_client.post(
        "/api/v1/credentials",
        json={"name": "dup", "type": "http_bearer", "plaintext": {"token": "t"}},
    )
    auth = await authed_client.post(
        "/api/v1/oauth/google/authorize",
        json={"credential_name": "dup", "scopes": ["scope"]},
    )
    state = parse_qs(urlparse(auth.json()["authorize_url"]).query)["state"][0]

    r = await authed_client.get(
        "/api/v1/oauth/google/callback",
        params={"code": "c", "state": state},
        follow_redirects=False,
    )
    assert "reason=duplicate_name" in r.headers["location"]


async def test_callback_missing_refresh_token_redirects_error(authed_client):
    authed_client._transport.app.state.google_oauth_client = _make_mock_client(
        _ok_token_handler(with_refresh=False)
    )
    auth = await authed_client.post(
        "/api/v1/oauth/google/authorize",
        json={"credential_name": "no-rt", "scopes": ["scope"]},
    )
    state = parse_qs(urlparse(auth.json()["authorize_url"]).query)["state"][0]

    r = await authed_client.get(
        "/api/v1/oauth/google/callback",
        params={"code": "c", "state": state},
        follow_redirects=False,
    )
    assert "reason=no_refresh_token" in r.headers["location"]


# -------------------------------------------------- /credentials/{id}/reauth


async def test_reauth_missing_credential_404(authed_client):
    from uuid import uuid4
    r = await authed_client.post(
        f"/api/v1/credentials/{uuid4()}/reauth", json={}
    )
    assert r.status_code == 404


async def test_reauth_updates_tokens_on_existing_credential(authed_client):
    authed_client._transport.app.state.google_oauth_client = _make_mock_client(
        _ok_token_handler()
    )
    # Step 1 — create the credential via first-time authorize/callback.
    auth = await authed_client.post(
        "/api/v1/oauth/google/authorize",
        json={
            "credential_name": "gm-reauth",
            "scopes": ["https://www.googleapis.com/auth/gmail.send"],
        },
    )
    state = parse_qs(urlparse(auth.json()["authorize_url"]).query)["state"][0]
    cb = await authed_client.get(
        "/api/v1/oauth/google/callback",
        params={"code": "c1", "state": state},
        follow_redirects=False,
    )
    cred_id = parse_qs(urlparse(cb.headers["location"]).query)["credential_id"][0]

    # Step 2 — kick off reauth for that credential.
    r = await authed_client.post(
        f"/api/v1/credentials/{cred_id}/reauth", json={}
    )
    assert r.status_code == 200
    reauth_url = r.json()["authorize_url"]
    reauth_state = parse_qs(urlparse(reauth_url).query)["state"][0]

    # Step 3 — complete the reauth callback; handler returns a fresh
    # access_token. The store update goes through update_oauth_tokens
    # (not a new store_google_oauth), so no duplicate-name collision.
    cb2 = await authed_client.get(
        "/api/v1/oauth/google/callback",
        params={"code": "c2", "state": reauth_state},
        follow_redirects=False,
    )
    assert cb2.status_code in (302, 307)
    assert "oauth=success" in cb2.headers["location"]
    # Same credential_id comes back — update-in-place, not a new row.
    assert f"credential_id={cred_id}" in cb2.headers["location"]
