"""Unit tests for OAuthStateSigner — ADR-019 CSRF defence.

Pure in-process — no Postgres, no network. Uses monkeypatch on `time.time`
inside the signer's module so we can fast-forward past the TTL.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.services import oauth_state as mod
from app.services.oauth_state import (
    InvalidStateError,
    OAuthStateSigner,
)


def _signer() -> OAuthStateSigner:
    return OAuthStateSigner(secret="unit-test-secret", ttl_seconds=600)


def test_roundtrip_returns_claims():
    s = _signer()
    owner = uuid4()
    token = s.issue(
        owner,
        credential_name="work gmail",
        scopes=["https://www.googleapis.com/auth/gmail.send"],
        return_to="/workflows/new",
    )
    claims = s.verify(token)
    assert claims.owner_id == owner
    assert claims.credential_name == "work gmail"
    assert claims.scopes == ("https://www.googleapis.com/auth/gmail.send",)
    assert claims.return_to == "/workflows/new"


def test_return_to_optional():
    s = _signer()
    owner = uuid4()
    token = s.issue(owner, credential_name="gmail", scopes=["gmail.send"])
    assert s.verify(token).return_to is None


def test_missing_credential_name_rejected():
    s = _signer()
    # Simulate a maliciously crafted payload (re-sign with our own HMAC
    # so only the semantic check catches it).
    import base64, hmac, hashlib, json
    body = base64.urlsafe_b64encode(
        json.dumps({
            "owner_id": str(uuid4()),
            "scopes": ["gmail.send"],
            "nonce": "x",
            "iat": 99999999999,
            "return_to": None,
        }).encode()
    ).rstrip(b"=").decode("ascii")
    sig = base64.urlsafe_b64encode(
        hmac.new(b"unit-test-secret", body.encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode("ascii")
    with pytest.raises(InvalidStateError, match="bad credential_name"):
        s.verify(f"{body}.{sig}")


def test_tampered_payload_rejected():
    s = _signer()
    token = s.issue(uuid4(), credential_name="gmail", scopes=["gmail.send"])
    body, sig = token.split(".", 1)
    # Flip one byte in the payload — signature will no longer match.
    tampered = body[:-1] + ("A" if body[-1] != "A" else "B") + "." + sig
    with pytest.raises(InvalidStateError, match="bad signature"):
        s.verify(tampered)


def test_different_secret_rejected():
    issued = OAuthStateSigner(secret="secret-a").issue(
        uuid4(), credential_name="gmail", scopes=["gmail.send"]
    )
    with pytest.raises(InvalidStateError, match="bad signature"):
        OAuthStateSigner(secret="secret-b").verify(issued)


def test_expired_token_rejected(monkeypatch):
    s = OAuthStateSigner(secret="s", ttl_seconds=60)
    t0 = 1_700_000_000.0
    monkeypatch.setattr(mod, "time", lambda: t0)
    token = s.issue(uuid4(), credential_name="gmail", scopes=["gmail.send"])
    # Jump 61 seconds past issuance — beyond the 60s TTL.
    monkeypatch.setattr(mod, "time", lambda: t0 + 61)
    with pytest.raises(InvalidStateError, match="expired"):
        s.verify(token)


def test_replay_rejected(monkeypatch):
    s = _signer()
    t0 = 1_700_000_000.0
    monkeypatch.setattr(mod, "time", lambda: t0)
    token = s.issue(uuid4(), credential_name="gmail", scopes=["gmail.send"])
    s.verify(token)  # first use OK
    with pytest.raises(InvalidStateError, match="replay"):
        s.verify(token)


def test_malformed_token_rejected():
    s = _signer()
    with pytest.raises(InvalidStateError, match="malformed state"):
        s.verify("not-a-valid-token")


def test_malformed_payload_rejected():
    s = _signer()
    # Construct a token with a valid signature over non-JSON payload.
    import base64, hmac, hashlib
    body = base64.urlsafe_b64encode(b"not json").rstrip(b"=").decode("ascii")
    sig_bytes = hmac.new(b"unit-test-secret", body.encode("ascii"), hashlib.sha256).digest()
    sig = base64.urlsafe_b64encode(sig_bytes).rstrip(b"=").decode("ascii")
    with pytest.raises(InvalidStateError, match="malformed payload"):
        s.verify(f"{body}.{sig}")


def test_expired_nonce_is_gc_d(monkeypatch):
    # After TTL passes, the LRU entry is evicted, so a *new* token with a
    # recycled nonce (won't happen in practice — they're random) would not
    # be falsely flagged as replay. We can't easily force a collision, but
    # we can at least assert the cache doesn't grow unbounded by firing
    # many tokens past TTL.
    s = OAuthStateSigner(secret="s", ttl_seconds=10, nonce_cache_size=1000)
    t = [1_700_000_000.0]
    monkeypatch.setattr(mod, "time", lambda: t[0])
    for _ in range(5):
        token = s.issue(uuid4(), credential_name="gmail", scopes=["gmail.send"])
        s.verify(token)
    t[0] += 20  # advance past TTL
    # New verify should evict the 5 stale nonces from the cache.
    token = s.issue(uuid4(), credential_name="gmail", scopes=["gmail.send"])
    s.verify(token)
    assert len(s._seen) == 1  # cache trimmed to the live token only
