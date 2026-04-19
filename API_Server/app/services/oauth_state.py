"""OAuth CSRF `state` helper — ADR-019.

Google's Authorization Code flow hands the `state` parameter back to the
callback verbatim. We use it for two things:

1. **Binding** — the state carries the `owner_id` so the callback can
   attribute the resulting credential to the user who started the flow,
   without trusting a cookie that can be forged by a cross-site attacker.
2. **CSRF** — a short-lived HMAC signature (keyed on `JWT_SECRET`) plus a
   random nonce that we remember in an LRU blacklist for the token's
   lifetime. An attacker who intercepts one callback URL cannot re-use it
   after 10 minutes, and cannot replay it even within the window.

ADR-019 §"deferred" notes that the in-memory LRU only holds for a single
API_Server instance — horizontal scaling will move this blacklist to
Redis SETNX or a small `oauth_state_nonces` table. Until then the single-
instance Cloud Run config makes it sufficient.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from collections import OrderedDict
from dataclasses import dataclass
from time import time
from uuid import UUID


class InvalidStateError(Exception):
    """Raised when the callback's `state` fails signature, TTL, or replay checks.

    The router maps this to a 400 response; the user is shown a generic
    "OAuth session expired, please try again" page — we never echo back
    *why* the state failed (attacker would learn whether the signature
    was valid vs. just replayed).
    """


@dataclass(frozen=True)
class StateClaims:
    owner_id: UUID
    credential_name: str
    scopes: tuple[str, ...]
    return_to: str | None
    # Set on re-consent flows so the callback knows to call
    # `update_oauth_tokens` on an existing row instead of creating a new
    # one (and to clear `needs_reauth`).
    existing_credential_id: UUID | None = None


class OAuthStateSigner:
    def __init__(
        self,
        *,
        secret: str,
        ttl_seconds: int = 600,
        nonce_cache_size: int = 10_000,
    ) -> None:
        self._key = secret.encode("utf-8")
        self._ttl = ttl_seconds
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._cache_size = nonce_cache_size

    def issue(
        self,
        owner_id: UUID,
        *,
        credential_name: str,
        scopes: list[str],
        return_to: str | None = None,
        existing_credential_id: UUID | None = None,
    ) -> str:
        payload = {
            "owner_id": str(owner_id),
            "credential_name": credential_name,
            "scopes": list(scopes),
            "nonce": secrets.token_urlsafe(16),
            "iat": int(time()),
            "return_to": return_to,
            "existing_credential_id": (
                str(existing_credential_id) if existing_credential_id else None
            ),
        }
        body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        sig = _b64(self._sign(body))
        return f"{body}.{sig}"

    def verify(self, token: str) -> StateClaims:
        try:
            body, sig = token.split(".", 1)
        except ValueError:
            raise InvalidStateError("malformed state")

        expected = _b64(self._sign(body))
        # constant-time compare to avoid leaking sig shape via timing.
        if not hmac.compare_digest(expected, sig):
            raise InvalidStateError("bad signature")

        try:
            payload = json.loads(_b64_decode(body))
        except (ValueError, json.JSONDecodeError):
            raise InvalidStateError("malformed payload")

        now = time()
        iat = payload.get("iat")
        if not isinstance(iat, int) or now - iat > self._ttl:
            raise InvalidStateError("expired")

        nonce = payload.get("nonce")
        if not isinstance(nonce, str) or not nonce:
            raise InvalidStateError("missing nonce")

        self._evict_expired(now)
        if nonce in self._seen:
            raise InvalidStateError("replay")
        self._seen[nonce] = iat
        # Bound the cache — the oldest entry is the one most likely to be
        # already expired by TTL, so dropping it doesn't weaken the replay
        # guarantee we care about (the live 10-min window).
        while len(self._seen) > self._cache_size:
            self._seen.popitem(last=False)

        try:
            owner_id = UUID(payload["owner_id"])
        except (KeyError, ValueError, TypeError):
            raise InvalidStateError("bad owner_id")

        credential_name = payload.get("credential_name")
        if not isinstance(credential_name, str) or not credential_name:
            raise InvalidStateError("bad credential_name")

        scopes_raw = payload.get("scopes")
        if not isinstance(scopes_raw, list) or not all(
            isinstance(x, str) for x in scopes_raw
        ):
            raise InvalidStateError("bad scopes")

        return_to = payload.get("return_to")
        if return_to is not None and not isinstance(return_to, str):
            raise InvalidStateError("bad return_to")

        existing_raw = payload.get("existing_credential_id")
        if existing_raw is not None:
            try:
                existing_credential_id = UUID(existing_raw)
            except (ValueError, TypeError):
                raise InvalidStateError("bad existing_credential_id")
        else:
            existing_credential_id = None

        return StateClaims(
            owner_id=owner_id,
            credential_name=credential_name,
            scopes=tuple(scopes_raw),
            return_to=return_to,
            existing_credential_id=existing_credential_id,
        )

    def _sign(self, body: str) -> bytes:
        return hmac.new(self._key, body.encode("ascii"), hashlib.sha256).digest()

    def _evict_expired(self, now: float) -> None:
        cutoff = now - self._ttl
        while self._seen:
            oldest_nonce, oldest_iat = next(iter(self._seen.items()))
            if oldest_iat < cutoff:
                self._seen.popitem(last=False)
            else:
                break


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)
