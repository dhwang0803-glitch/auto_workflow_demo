"""GoogleWorkspaceNode — shared base for Gmail/Drive/Sheets/Docs/Slides/Calendar (ADR-019 Phase 4).

Concrete Google Workspace nodes subclass this to reuse the OAuth2 refresh
machinery. The base class owns:

  1. Freshness check against `oauth_metadata.token_expires_at`.
  2. Per-credential `asyncio.Lock` so concurrent node executions sharing a
     credential serialize their refresh attempts (Google rotates refresh
     tokens — last-write-wins is fine within one process, but duplicate
     refresh round-trips are wasteful and occasionally rate-limited).
  3. `invalid_grant` → `CredentialStore.mark_needs_reauth()` translation
     so the UI can surface a reauth prompt instead of silently failing
     every execution until the user notices.
  4. One long-lived `httpx.AsyncClient` shared across all concrete node
     calls in a Worker process (TLS reuse; Google APIs share the same
     `googleapis.com` wildcard cert pool).

Dependencies come in via `configure()` at Worker bootstrap — the
`NodeRegistry` insists on a no-arg constructor, so we can't take them
through `__init__`. Tests call `configure()` with fakes directly.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

import httpx

from auto_workflow_database.repositories.base import CredentialStore

from src.nodes.base import BaseNode
from src.services.google_oauth_client import GoogleOAuthClient, OAuthTokenError

# A 60-second buffer: if the token expires in the next minute we refresh
# eagerly rather than risk a 401 mid-API-call (especially for batch nodes
# that kick off several Google requests back-to-back).
_EXPIRY_BUFFER = timedelta(seconds=60)


class GoogleWorkspaceNode(BaseNode):
    _credential_store: CredentialStore | None = None
    _oauth_client: GoogleOAuthClient | None = None
    _http_client: httpx.AsyncClient | None = None
    # Class-level so all concrete instances share the same per-credential
    # lock. `NodeRegistry.get(type)()` hands out fresh instances each call,
    # so instance-level locks would never collide.
    _locks: dict[UUID, asyncio.Lock] = {}

    @classmethod
    def configure(
        cls,
        *,
        credential_store: CredentialStore,
        oauth_client: GoogleOAuthClient,
        http_client: httpx.AsyncClient,
    ) -> None:
        cls._credential_store = credential_store
        cls._oauth_client = oauth_client
        cls._http_client = http_client

    @classmethod
    def reset(cls) -> None:
        # Used between test cases to clear the per-credential lock dict so
        # state doesn't bleed across tests reusing the same cred UUIDs.
        cls._credential_store = None
        cls._oauth_client = None
        cls._http_client = None
        cls._locks = {}

    @property
    def node_type(self) -> str:  # pragma: no cover — abstract shim
        raise NotImplementedError("subclasses must override node_type")

    async def execute(self, input_data: dict, config: dict) -> dict:  # pragma: no cover
        raise NotImplementedError("subclasses must override execute()")

    # --------------------------------------------------------------- helpers

    def _get_lock(self, credential_id: UUID) -> asyncio.Lock:
        lock = self._locks.get(credential_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[credential_id] = lock
        return lock

    async def _ensure_fresh_token(self, credential_id: UUID) -> str:
        """Return a valid access_token for `credential_id`.

        Fast path reads the credential once; if the access token is still
        good (>60s from expiry), returns it without grabbing the lock.
        Otherwise takes the per-credential lock, re-reads (double-checked
        locking — another coroutine may have refreshed while we waited),
        calls Google's /token refresh endpoint, persists the rotated
        tokens, and returns the new access_token.
        """
        store = self._credential_store
        client = self._oauth_client
        if store is None or client is None:
            raise RuntimeError(
                "GoogleWorkspaceNode not configured — call configure() at bootstrap"
            )

        plaintext = await store.retrieve(credential_id)
        md = plaintext.get("oauth_metadata") or {}
        token = md.get("access_token")
        if token and _is_fresh(md):
            return token

        async with self._get_lock(credential_id):
            plaintext = await store.retrieve(credential_id)
            md = plaintext.get("oauth_metadata") or {}
            token = md.get("access_token")
            if token and _is_fresh(md):
                return token

            refresh_token = plaintext.get("refresh_token")
            if not refresh_token:
                # Happens if the row was created via a non-OAuth path or
                # the refresh token was cleared — caller can't recover.
                raise RuntimeError(
                    f"credential {credential_id} has no refresh_token"
                )

            try:
                tokens = await client.refresh_access_token(refresh_token)
            except OAuthTokenError as exc:
                if exc.error == "invalid_grant":
                    # Refresh token was revoked or the user removed the app.
                    # Flag the credential so the API can prompt reauth and
                    # stop wasting refresh attempts on every execution.
                    await store.mark_needs_reauth(credential_id)
                raise

            new_access = tokens["access_token"]
            expires_in = int(tokens.get("expires_in", 3599))
            new_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
            # Google only rotates refresh_token when it feels like it —
            # most refresh responses omit the field. Persist only when
            # present so we don't clobber the stored one with None.
            new_refresh = tokens.get("refresh_token")

            await store.update_oauth_tokens(
                credential_id,
                access_token=new_access,
                token_expires_at=new_expiry,
                refresh_token=new_refresh,
            )
            return new_access


def _is_fresh(oauth_metadata: dict) -> bool:
    raw = oauth_metadata.get("token_expires_at")
    if not raw:
        return False
    # Stored as ISO-8601 by API_Server (update_oauth_tokens / callback).
    # `fromisoformat` handles the "+00:00" offset we emit.
    try:
        expiry = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) + _EXPIRY_BUFFER < expiry
