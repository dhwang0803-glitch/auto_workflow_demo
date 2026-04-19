"""GoogleWorkspaceNode unit tests — ADR-019 Phase 4.

No live Postgres, no network. `httpx.MockTransport` intercepts the POST
to Google's /token endpoint so we can drive the refresh flow through
every branch the base class owns.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
import pytest

from src.nodes.google_workspace import GoogleWorkspaceNode
from src.services.google_oauth_client import (
    GOOGLE_TOKEN_URL,
    GoogleOAuthClient,
    OAuthTokenError,
)
from tests.fakes import InMemoryCredentialStore


class _Subject(GoogleWorkspaceNode):
    """Concrete subclass so we can instantiate — the base has no node_type."""

    @property
    def node_type(self) -> str:
        return "google_workspace_test_subject"

    async def execute(self, input_data, config):  # unused — we call _ensure_fresh_token directly
        return {}


def _md(*, access_token: str, expires_in_seconds: int, **extra) -> dict:
    return {
        "access_token": access_token,
        "token_expires_at": (
            datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)
        ).isoformat(),
        "scopes": ["https://www.googleapis.com/auth/gmail.send"],
        "account_email": "u@example.com",
        **extra,
    }


def _ok_refresh_handler(new_access: str = "at-new", *, with_rotated_refresh: bool = False):
    def handler(req):
        assert str(req.url) == GOOGLE_TOKEN_URL
        body = dict(p.split("=", 1) for p in req.content.decode().split("&"))
        assert body["grant_type"] == "refresh_token"
        out = {"access_token": new_access, "expires_in": 3599, "scope": "..."}
        if with_rotated_refresh:
            out["refresh_token"] = "rt-rotated"
        return httpx.Response(200, json=out)
    return handler


def _invalid_grant_handler(_req):
    return httpx.Response(
        400,
        json={"error": "invalid_grant", "error_description": "Token revoked"},
    )


async def _configure(handler) -> tuple[InMemoryCredentialStore, UUID]:
    store = InMemoryCredentialStore()
    owner = uuid4()
    cid = await store.store_google_oauth(
        owner, "gm", refresh_token="rt-old",
        oauth_metadata=_md(access_token="at-old", expires_in_seconds=3600),
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = GoogleOAuthClient(
        client_id="cid", client_secret="sec", http_client=http,
    )
    GoogleWorkspaceNode.configure(
        credential_store=store, oauth_client=client, http_client=http,
    )
    return store, cid


@pytest.fixture(autouse=True)
def _reset_base_state():
    # Each test gets a clean slate — no bleed-over of locks or deps.
    yield
    GoogleWorkspaceNode.reset()


# ----------------------------------------------------------------- freshness


async def test_fresh_token_returns_existing_no_refresh(httpx_mock):
    calls = {"n": 0}

    def handler(_req):
        calls["n"] += 1
        return httpx.Response(200, json={"access_token": "would-never-use"})

    store, cid = await _configure(handler)
    token = await _Subject()._ensure_fresh_token(cid)
    assert token == "at-old"
    assert calls["n"] == 0  # no refresh call issued — fast path


async def test_expired_token_triggers_refresh_and_persists():
    store = InMemoryCredentialStore()
    owner = uuid4()
    cid = await store.store_google_oauth(
        owner, "gm", refresh_token="rt-old",
        oauth_metadata=_md(access_token="at-old", expires_in_seconds=-10),  # already expired
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(_ok_refresh_handler("at-new")))
    GoogleWorkspaceNode.configure(
        credential_store=store,
        oauth_client=GoogleOAuthClient(client_id="c", client_secret="s", http_client=http),
        http_client=http,
    )

    token = await _Subject()._ensure_fresh_token(cid)
    assert token == "at-new"

    stored = await store.retrieve(cid)
    assert stored["oauth_metadata"]["access_token"] == "at-new"
    # Refresh-token rotation wasn't emitted, so the original stays.
    assert stored["refresh_token"] == "rt-old"


async def test_near_expiry_within_buffer_triggers_refresh():
    store = InMemoryCredentialStore()
    owner = uuid4()
    cid = await store.store_google_oauth(
        owner, "gm", refresh_token="rt",
        # 30s left — inside the 60s expiry buffer, so we refresh eagerly.
        oauth_metadata=_md(access_token="at-old", expires_in_seconds=30),
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(_ok_refresh_handler("at-new")))
    GoogleWorkspaceNode.configure(
        credential_store=store,
        oauth_client=GoogleOAuthClient(client_id="c", client_secret="s", http_client=http),
        http_client=http,
    )

    assert await _Subject()._ensure_fresh_token(cid) == "at-new"


async def test_rotated_refresh_token_is_persisted():
    store = InMemoryCredentialStore()
    owner = uuid4()
    cid = await store.store_google_oauth(
        owner, "gm", refresh_token="rt-old",
        oauth_metadata=_md(access_token="at-old", expires_in_seconds=-1),
    )
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(_ok_refresh_handler("at-new", with_rotated_refresh=True))
    )
    GoogleWorkspaceNode.configure(
        credential_store=store,
        oauth_client=GoogleOAuthClient(client_id="c", client_secret="s", http_client=http),
        http_client=http,
    )

    await _Subject()._ensure_fresh_token(cid)
    stored = await store.retrieve(cid)
    assert stored["refresh_token"] == "rt-rotated"


# ---------------------------------------------------------------- error paths


async def test_invalid_grant_marks_needs_reauth_and_raises():
    store, cid = await _configure(_invalid_grant_handler)
    # Force refresh path by backdating expiry.
    await store.update_oauth_tokens(
        cid, access_token="at", token_expires_at=datetime.now(timezone.utc) - timedelta(seconds=10),
    )

    with pytest.raises(OAuthTokenError) as exc_info:
        await _Subject()._ensure_fresh_token(cid)
    assert exc_info.value.error == "invalid_grant"

    stored = await store.retrieve(cid)
    assert stored["oauth_metadata"].get("needs_reauth") is True


async def test_unconfigured_raises():
    GoogleWorkspaceNode.reset()
    with pytest.raises(RuntimeError, match="not configured"):
        await _Subject()._ensure_fresh_token(uuid4())


async def test_missing_refresh_token_raises():
    store = InMemoryCredentialStore()
    owner = uuid4()
    cid = await store.store_google_oauth(
        owner, "gm", refresh_token="",  # empty
        oauth_metadata=_md(access_token="at", expires_in_seconds=-1),
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(_ok_refresh_handler()))
    GoogleWorkspaceNode.configure(
        credential_store=store,
        oauth_client=GoogleOAuthClient(client_id="c", client_secret="s", http_client=http),
        http_client=http,
    )
    with pytest.raises(RuntimeError, match="no refresh_token"):
        await _Subject()._ensure_fresh_token(cid)


# ------------------------------------------------------------ concurrency


async def test_concurrent_refresh_is_serialized_one_call():
    """Five simultaneous _ensure_fresh_token calls against the same expired
    credential must result in exactly one /token refresh — the per-credential
    lock + double-checked read guarantees later callers see the fresh token.
    """
    call_count = {"n": 0}

    def handler(req):
        call_count["n"] += 1
        return httpx.Response(
            200, json={"access_token": f"at-new-{call_count['n']}", "expires_in": 3599}
        )

    store = InMemoryCredentialStore()
    owner = uuid4()
    cid = await store.store_google_oauth(
        owner, "gm", refresh_token="rt",
        oauth_metadata=_md(access_token="at-old", expires_in_seconds=-1),
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    GoogleWorkspaceNode.configure(
        credential_store=store,
        oauth_client=GoogleOAuthClient(client_id="c", client_secret="s", http_client=http),
        http_client=http,
    )

    tokens = await asyncio.gather(
        *[_Subject()._ensure_fresh_token(cid) for _ in range(5)]
    )
    # One refresh round-trip, five callers all received the same new token.
    assert call_count["n"] == 1
    assert len(set(tokens)) == 1
    assert tokens[0] == "at-new-1"
