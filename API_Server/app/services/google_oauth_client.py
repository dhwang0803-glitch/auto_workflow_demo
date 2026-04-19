"""Google OAuth2 token-endpoint client — ADR-019.

Talks only to `https://oauth2.googleapis.com/token`. The authorize URL is
built in the router (it's a simple querystring and doesn't need a class),
and user-facing redirects live in the web layer. This module exists purely
to centralize credential-bearing POST calls so tests can inject an
`httpx.AsyncClient` with a `MockTransport` and exercise error paths
(expired codes, revoked refresh tokens) without network I/O.
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
        redirect_uri: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        # Injected client is used as-is (test-owned lifecycle). When absent
        # we open a short-lived one per call — fine for OAuth callbacks,
        # which happen once per credential, not per node execution.
        self._http = http_client

    async def exchange_code(self, code: str) -> dict:
        return await self._post_token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "redirect_uri": self._redirect_uri,
            }
        )

    async def refresh_access_token(self, refresh_token: str) -> dict:
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
