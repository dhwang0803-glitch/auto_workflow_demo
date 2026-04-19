"""Unit tests for GoogleOAuthClient — ADR-019.

No live Postgres, no network. `httpx.MockTransport` intercepts the POST
to Google's /token endpoint so we can exercise success + all error
branches the caller has to distinguish (invalid_grant → needs_reauth).
"""
from __future__ import annotations

import httpx
import pytest

from app.services.google_oauth_client import (
    GOOGLE_TOKEN_URL,
    GoogleOAuthClient,
    OAuthTokenError,
)


def _client_with_transport(handler) -> tuple[GoogleOAuthClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def _capture(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    transport = httpx.MockTransport(_capture)
    http = httpx.AsyncClient(transport=transport)
    client = GoogleOAuthClient(
        client_id="cid",
        client_secret="sec",
        redirect_uri="https://api.example.com/oauth/google/callback",
        http_client=http,
    )
    return client, seen


@pytest.mark.asyncio
async def test_exchange_code_success():
    def handler(req):
        assert str(req.url) == GOOGLE_TOKEN_URL
        body = dict(p.split("=", 1) for p in req.content.decode().split("&"))
        assert body["grant_type"] == "authorization_code"
        assert body["code"] == "abc123"
        assert body["client_id"] == "cid"
        return httpx.Response(
            200,
            json={
                "access_token": "at",
                "refresh_token": "rt",
                "expires_in": 3599,
                "scope": "https://www.googleapis.com/auth/gmail.send",
                "token_type": "Bearer",
            },
        )

    client, seen = _client_with_transport(handler)
    out = await client.exchange_code("abc123")
    assert out["access_token"] == "at"
    assert out["refresh_token"] == "rt"
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_refresh_access_token_success():
    def handler(req):
        body = dict(p.split("=", 1) for p in req.content.decode().split("&"))
        assert body["grant_type"] == "refresh_token"
        assert body["refresh_token"] == "rt"
        # Refresh requests must NOT include redirect_uri (Google rejects it).
        assert "redirect_uri" not in body
        return httpx.Response(
            200,
            json={"access_token": "at2", "expires_in": 3599, "scope": "…"},
        )

    client, _ = _client_with_transport(handler)
    out = await client.refresh_access_token("rt")
    assert out["access_token"] == "at2"


@pytest.mark.asyncio
async def test_invalid_grant_raises_with_error_code():
    def handler(_req):
        return httpx.Response(
            400,
            json={
                "error": "invalid_grant",
                "error_description": "Token has been expired or revoked.",
            },
        )

    client, _ = _client_with_transport(handler)
    with pytest.raises(OAuthTokenError) as exc_info:
        await client.refresh_access_token("rt")
    # Router translates .error == "invalid_grant" into mark_needs_reauth.
    assert exc_info.value.error == "invalid_grant"
    assert "revoked" in exc_info.value.description


@pytest.mark.asyncio
async def test_unknown_error_body_still_raises():
    def handler(_req):
        return httpx.Response(500, json={})

    client, _ = _client_with_transport(handler)
    with pytest.raises(OAuthTokenError) as exc_info:
        await client.exchange_code("x")
    assert exc_info.value.error == "unknown_error"
