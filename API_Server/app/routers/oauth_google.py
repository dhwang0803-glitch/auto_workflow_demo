"""Google OAuth Authorization Code flow — ADR-019.

Two endpoints:

- `POST /authorize` — the logged-in user asks us to start an OAuth flow
  for a named credential with a given scope list. We return the Google
  consent URL; the frontend redirects the browser there.
- `GET /callback` — Google redirects the browser back here with `code` +
  `state`. The user may no longer carry our Bearer token (it's an
  arbitrary redirect from `accounts.google.com`), so ownership is bound
  via the signed `state` parameter, not an auth header.

The callback is the single place that exchanges the code for tokens,
persists the credential via `CredentialStore.store_google_oauth`, and
redirects back to the UI. On invalid_grant from Google we surface a
user-visible error — there is no existing credential to mark
`needs_reauth` yet (first-time consent).
"""
from __future__ import annotations

from urllib.parse import urlencode
from uuid import UUID

from auto_workflow_database.repositories.base import User
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.exc import IntegrityError

from app.dependencies import get_current_user
from app.errors import DomainError, NotFoundError
from app.services.google_oauth_client import GoogleOAuthClient, OAuthTokenError
from app.services.oauth_state import InvalidStateError, OAuthStateSigner

router = APIRouter()

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"


class AuthorizeRequest(BaseModel):
    credential_name: str | None = Field(default=None, min_length=1, max_length=255)
    scopes: list[str] = Field(min_length=1)
    return_to: str | None = None
    # ADR-019 §2 incremental consent: when set, this flow EXTENDS an
    # existing google_oauth credential with additional scopes instead of
    # creating a new row. `credential_name` is taken from the existing
    # row in that case (frontend doesn't need to know it).
    extends_credential_id: UUID | None = None

    @model_validator(mode="after")
    def _exactly_one_target(self) -> "AuthorizeRequest":
        # The xor lets the schema cover both first-time and incremental
        # without two near-duplicate endpoints.
        if (self.credential_name is None) == (self.extends_credential_id is None):
            raise ValueError(
                "exactly one of credential_name or extends_credential_id required"
            )
        return self


class AuthorizeResponse(BaseModel):
    authorize_url: str


class OAuthConfigError(DomainError):
    """503 — Google OAuth client isn't configured on this deployment."""

    http_status = 503


class OAuthFlowError(DomainError):
    """400 — Google returned an error or the state failed validation.

    The message is intentionally generic to avoid telling an attacker
    which part of the check failed (signature vs. TTL vs. replay).
    """

    http_status = 400


def _get_state_signer(request: Request) -> OAuthStateSigner:
    return request.app.state.oauth_state_signer


def _get_google_client(request: Request) -> GoogleOAuthClient:
    client = request.app.state.google_oauth_client
    if client is None:
        raise OAuthConfigError("google oauth is not configured")
    return client


@router.post("/authorize", response_model=AuthorizeResponse)
async def authorize(
    body: AuthorizeRequest,
    request: Request,
    user: User = Depends(get_current_user),
    signer: OAuthStateSigner = Depends(_get_state_signer),
    client: GoogleOAuthClient = Depends(_get_google_client),
) -> AuthorizeResponse:
    credential_name = body.credential_name
    scopes = list(body.scopes)
    existing_id: UUID | None = None

    if body.extends_credential_id is not None:
        # Incremental path: verify ownership, merge requested scopes into
        # the credential's already-granted set. Google's
        # `include_granted_scopes=true` will merge server-side too, but we
        # send the explicit union so the consent screen shows the user the
        # full picture and our state's scopes match what we expect back.
        store = request.app.state.credential_store
        try:
            creds = await store.bulk_retrieve(
                [body.extends_credential_id], owner_id=user.id
            )
        except KeyError:
            raise NotFoundError("credential not found")

        plaintext = creds[body.extends_credential_id]
        metadata = plaintext.get("oauth_metadata") or {}
        existing_scopes = (
            metadata.get("granted_scopes") or metadata.get("scopes") or []
        )
        # Preserve order: existing scopes first, then any new ones not
        # already granted. dict.fromkeys keeps first-seen order.
        scopes = list(dict.fromkeys([*existing_scopes, *body.scopes]))

        for row in await store.list_by_owner(user.id):
            if row.id == body.extends_credential_id:
                credential_name = row.name
                break
        existing_id = body.extends_credential_id

    state = signer.issue(
        user.id,
        credential_name=credential_name,
        scopes=scopes,
        return_to=body.return_to,
        existing_credential_id=existing_id,
    )
    # ADR-019: access_type=offline + prompt=consent guarantees Google
    # issues a refresh_token even when the user already granted scopes.
    return AuthorizeResponse(
        authorize_url=build_authorize_url(
            settings=request.app.state.settings,
            state=state,
            scopes=scopes,
        )
    )


@router.get("/callback")
async def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    signer: OAuthStateSigner = Depends(_get_state_signer),
    client: GoogleOAuthClient = Depends(_get_google_client),
) -> RedirectResponse:
    # User clicked "Deny" on the consent screen, or Google refused the
    # request before any code was issued.
    if error:
        return _redirect_with_error(request, None, reason=error)

    if not code or not state:
        raise OAuthFlowError("missing code or state")

    try:
        claims = signer.verify(state)
    except InvalidStateError:
        raise OAuthFlowError("invalid state")

    try:
        token_resp = await client.exchange_code(code)
    except OAuthTokenError as e:
        return _redirect_with_error(request, claims.return_to, reason=e.error)

    refresh_token = token_resp.get("refresh_token")
    expires_in = int(token_resp.get("expires_in", 0))
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    store = request.app.state.credential_store

    if claims.existing_credential_id is not None:
        # Re-consent / incremental-consent path: update tokens on the
        # existing row. Google omits refresh_token when it merges into a
        # prior grant (ADR-019 §2 — incremental consent doesn't rotate
        # the refresh token); we just refresh access_token + the merged
        # scope list and keep the stored refresh_token as-is.
        granted = token_resp.get("scope", "").split() or None
        try:
            await store.update_oauth_tokens(
                claims.existing_credential_id,
                access_token=token_resp["access_token"],
                token_expires_at=expires_at,
                refresh_token=refresh_token,
                granted_scopes=granted,
            )
        except KeyError:
            return _redirect_with_error(
                request, claims.return_to, reason="credential_not_found"
            )
        return _redirect_with_success(
            request, claims.return_to, claims.existing_credential_id
        )

    # First-time consent: a refresh_token is mandatory. We always pass
    # prompt=consent + access_type=offline, so Google will issue one.
    if not refresh_token:
        return _redirect_with_error(
            request, claims.return_to, reason="no_refresh_token"
        )

    metadata = {
        "access_token": token_resp["access_token"],
        "token_expires_at": expires_at.isoformat(),
        "scopes": list(claims.scopes),
        "granted_scopes": token_resp.get("scope", "").split(),
    }
    try:
        cred_id = await store.store_google_oauth(
            claims.owner_id,
            claims.credential_name,
            refresh_token=refresh_token,
            oauth_metadata=metadata,
        )
    except IntegrityError:
        return _redirect_with_error(
            request, claims.return_to, reason="duplicate_name"
        )

    return _redirect_with_success(request, claims.return_to, cred_id)


def build_authorize_url(
    *, settings, state: str, scopes: list[str]
) -> str:
    """Shared by /authorize and /credentials/{id}/reauth — centralizes the
    set of Google consent-URL parameters we care about (offline,
    force-consent, incremental scopes)."""
    params = {
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": settings.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    return f"{GOOGLE_AUTHORIZE_URL}?{urlencode(params)}"


def _redirect_with_success(request: Request, return_to: str | None, cred_id) -> RedirectResponse:
    base = return_to or f"{request.app.state.settings.app_base_url}/credentials"
    sep = "&" if "?" in base else "?"
    return RedirectResponse(url=f"{base}{sep}oauth=success&credential_id={cred_id}")


def _redirect_with_error(request: Request, return_to: str | None, *, reason: str) -> RedirectResponse:
    base = return_to or f"{request.app.state.settings.app_base_url}/credentials"
    sep = "&" if "?" in base else "?"
    return RedirectResponse(url=f"{base}{sep}oauth=error&reason={reason}")
