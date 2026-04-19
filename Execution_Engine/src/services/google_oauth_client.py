"""Google OAuth2 token-endpoint client — ADR-019 (Worker copy).

Mirrors API_Server/app/services/google_oauth_client.py but lives in the
Execution_Engine tree because the Worker needs the *refresh* path to
rotate access tokens before calling Google APIs. The authorize/exchange
paths stay on the API_Server side.

Kept as a standalone file (rather than cross-imported from API_Server)
because Execution_Engine is a separately deployable unit — Celery Worker
and Agent images MUST NOT pull in FastAPI + all API_Server deps.
"""
from __future__ import annotations

import httpx

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


class OAuthTokenError(Exception):
    """Non-2xx response from Google's /token endpoint.

    `error == "invalid_grant"` is the signal that callers should translate
    into `CredentialStore.mark_needs_reauth` — the refresh token was
    revoked or the user removed the app from their Google account.
    """

    def __init__(self, error: str, description: str = "") -> None:
        self.error = error
        self.description = description
        super().__init__(f"{error}: {description}" if description else error)


class GoogleOAuthClient:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        # Injected client is test-owned. Production Worker shares one
        # long-lived AsyncClient across refreshes (cheap vs. per-call TLS).
        self._http = http_client

    async def refresh_access_token(self, refresh_token: str) -> dict:
        # redirect_uri is intentionally omitted — Google rejects it on
        # refresh requests (only used on authorization_code exchange).
        return await self._post_token(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            }
        )

    async def _post_token(self, data: dict) -> dict:
        if self._http is not None:
            return await self._call(self._http, data)
        async with httpx.AsyncClient(timeout=10.0) as http:
            return await self._call(http, data)

    @staticmethod
    async def _call(http: httpx.AsyncClient, data: dict) -> dict:
        resp = await http.post(GOOGLE_TOKEN_URL, data=data)
        payload = resp.json()
        if resp.status_code != 200:
            raise OAuthTokenError(
                error=payload.get("error", "unknown_error"),
                description=payload.get("error_description", ""),
            )
        return payload
