"""ADR-019 Phase 2 — google_oauth lifecycle on InMemoryCredentialStore.

Fake-based unit tests: no DB required. The fake mirrors the Postgres
FernetCredentialStore contract so API_Server OAuth handlers and
Execution_Engine nodes can rely on either backend.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from tests.fakes import InMemoryCredentialStore


@pytest.fixture
def store():
    return InMemoryCredentialStore()


def _metadata(
    *,
    access_token: str = "ya29.fake-access",
    expires_at: datetime | None = None,
    scopes: tuple[str, ...] = ("gmail.send",),
    account_email: str = "user@example.com",
) -> dict:
    return {
        "provider": "google",
        "account_email": account_email,
        "scopes": list(scopes),
        "access_token": access_token,
        "token_expires_at": (
            expires_at or datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat(),
        "client_id_hash": "sha256:abc",
    }


async def test_store_google_oauth_persists_type_and_metadata(store):
    owner = uuid4()
    cid = await store.store_google_oauth(
        owner, "gmail-primary",
        refresh_token="1//refresh-token",
        oauth_metadata=_metadata(),
    )

    assert store._peek_type(cid) == "google_oauth"
    got = await store.retrieve(cid)
    assert got["refresh_token"] == "1//refresh-token"
    assert got["oauth_metadata"]["access_token"] == "ya29.fake-access"
    assert got["oauth_metadata"]["account_email"] == "user@example.com"


async def test_retrieve_non_oauth_has_no_metadata_key(store):
    owner = uuid4()
    cid = await store.store(
        owner, "slack", {"token": "xoxb"}, credential_type="slack_webhook",
    )
    got = await store.retrieve(cid)
    assert "oauth_metadata" not in got


async def test_update_oauth_tokens_refreshes_access_token(store):
    owner = uuid4()
    cid = await store.store_google_oauth(
        owner, "gmail",
        refresh_token="1//rt-original",
        oauth_metadata=_metadata(access_token="stale"),
    )

    new_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    await store.update_oauth_tokens(
        cid,
        access_token="ya29.fresh-token",
        token_expires_at=new_expiry,
    )

    got = await store.retrieve(cid)
    assert got["refresh_token"] == "1//rt-original"  # unchanged
    assert got["oauth_metadata"]["access_token"] == "ya29.fresh-token"
    assert got["oauth_metadata"]["token_expires_at"] == new_expiry.isoformat()


async def test_update_oauth_tokens_rotates_refresh_token(store):
    owner = uuid4()
    cid = await store.store_google_oauth(
        owner, "gmail",
        refresh_token="1//rt-old",
        oauth_metadata=_metadata(),
    )

    await store.update_oauth_tokens(
        cid,
        access_token="new-access",
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        refresh_token="1//rt-rotated",
    )

    got = await store.retrieve(cid)
    assert got["refresh_token"] == "1//rt-rotated"


async def test_update_oauth_tokens_clears_needs_reauth_flag(store):
    owner = uuid4()
    cid = await store.store_google_oauth(
        owner, "gmail",
        refresh_token="1//rt",
        oauth_metadata=_metadata(),
    )
    await store.mark_needs_reauth(cid)
    assert (await store.retrieve(cid))["oauth_metadata"]["needs_reauth"] is True

    await store.update_oauth_tokens(
        cid,
        access_token="new-access",
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    got = await store.retrieve(cid)
    assert "needs_reauth" not in got["oauth_metadata"]


async def test_mark_needs_reauth_sets_flag(store):
    owner = uuid4()
    cid = await store.store_google_oauth(
        owner, "gmail",
        refresh_token="1//rt",
        oauth_metadata=_metadata(),
    )

    await store.mark_needs_reauth(cid)

    got = await store.retrieve(cid)
    assert got["oauth_metadata"]["needs_reauth"] is True


async def test_update_oauth_tokens_unknown_credential_raises(store):
    with pytest.raises(KeyError):
        await store.update_oauth_tokens(
            uuid4(),
            access_token="x",
            token_expires_at=datetime.now(timezone.utc),
        )


async def test_mark_needs_reauth_unknown_credential_raises(store):
    with pytest.raises(KeyError):
        await store.mark_needs_reauth(uuid4())


async def test_oauth_credential_surfaces_in_list_by_owner(store):
    owner = uuid4()
    cid = await store.store_google_oauth(
        owner, "gmail-primary",
        refresh_token="1//rt",
        oauth_metadata=_metadata(),
    )

    rows = await store.list_by_owner(owner)

    assert len(rows) == 1
    assert rows[0].id == cid
    assert rows[0].type == "google_oauth"
    # Metadata DTO must not leak plaintext refresh_token or oauth_metadata.
    assert not hasattr(rows[0], "oauth_metadata")
    assert not hasattr(rows[0], "refresh_token")
