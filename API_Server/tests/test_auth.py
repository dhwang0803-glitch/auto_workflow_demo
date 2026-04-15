"""End-to-end auth tests against real Postgres.

Covers the register -> verify -> login -> me -> refresh flow plus the
error paths listed in PLAN_01 §8. The `client` fixture provides a
lifespan-started FastAPI app with a NoopEmailSender so we can assert on
captured verification links without SMTP.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import jwt
import pytest


async def _register(client, email: str = "user@example.com", password: str = "correct-horse-8"):
    return await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )


async def _verify_link_token(email_sender, to: str) -> str:
    match = next((link for (recipient, link) in email_sender.sent if recipient == to), None)
    assert match is not None, f"no verification email captured for {to}"
    qs = parse_qs(urlparse(match).query)
    return qs["token"][0]


async def _full_registration(client, email_sender, email: str, password: str):
    r = await _register(client, email=email, password=password)
    assert r.status_code == 201
    token = await _verify_link_token(email_sender, email)
    v = await client.get("/api/v1/auth/verify", params={"token": token})
    assert v.status_code == 200
    return v


# ------------------------------------------------------------------ register


async def test_register_creates_unverified_user_and_sends_email(client, email_sender):
    r = await _register(client, email="new@example.com")
    assert r.status_code == 201
    assert "verification email sent" in r.json()["message"]
    assert len(email_sender.sent) == 1
    recipient, link = email_sender.sent[0]
    assert recipient == "new@example.com"
    assert "/api/v1/auth/verify?token=" in link


async def test_register_duplicate_email_rejected(client):
    await _register(client, email="dup@example.com")
    r2 = await _register(client, email="dup@example.com")
    assert r2.status_code == 409
    assert r2.json()["detail"] == "email already registered"


async def test_register_weak_password_rejected(client):
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": "weak@example.com", "password": "short"},
    )
    assert r.status_code == 422


# -------------------------------------------------------------------- verify


async def test_verify_flips_is_verified(client, email_sender):
    await _full_registration(client, email_sender, "v1@example.com", "correct-horse-8")
    # Subsequent login should now succeed.
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": "v1@example.com", "password": "correct-horse-8"},
    )
    assert login.status_code == 200


async def test_verify_idempotent(client, email_sender):
    await _register(client, email="idem@example.com")
    token = await _verify_link_token(email_sender, "idem@example.com")
    first = await client.get("/api/v1/auth/verify", params={"token": token})
    second = await client.get("/api/v1/auth/verify", params={"token": token})
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["user_id"] == second.json()["user_id"]


async def test_verify_invalid_token_rejected(client):
    r = await client.get(
        "/api/v1/auth/verify", params={"token": "not-a-real-jwt-xxxxxxxx"}
    )
    assert r.status_code == 400


async def test_verify_wrong_purpose_rejected(client, email_sender):
    # Register + verify to get a clean user, then log in to get an *access*
    # token and try to use it at the verify endpoint. Purpose mismatch → 400.
    await _full_registration(client, email_sender, "wp@example.com", "correct-horse-8")
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": "wp@example.com", "password": "correct-horse-8"},
    )
    access = login.json()["access_token"]
    r = await client.get("/api/v1/auth/verify", params={"token": access})
    assert r.status_code == 400
    assert "purpose" in r.json()["detail"]


# --------------------------------------------------------------------- login


async def test_login_blocked_when_unverified(client):
    await _register(client, email="block@example.com", password="correct-horse-8")
    r = await client.post(
        "/api/v1/auth/login",
        data={"username": "block@example.com", "password": "correct-horse-8"},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "email not verified"


async def test_login_success_returns_access_token(client, email_sender):
    await _full_registration(client, email_sender, "ok@example.com", "correct-horse-8")
    r = await client.post(
        "/api/v1/auth/login",
        data={"username": "ok@example.com", "password": "correct-horse-8"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert isinstance(body["access_token"], str) and len(body["access_token"]) > 20


async def test_login_wrong_password_rejected(client, email_sender):
    await _full_registration(client, email_sender, "pw@example.com", "correct-horse-8")
    r = await client.post(
        "/api/v1/auth/login",
        data={"username": "pw@example.com", "password": "wrong-password"},
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid credentials"


# ---------------------------------------------------------------- me / refresh


async def test_me_returns_current_user_profile(client, email_sender):
    await _full_registration(client, email_sender, "me@example.com", "correct-horse-8")
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": "me@example.com", "password": "correct-horse-8"},
    )
    token = login.json()["access_token"]
    r = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "me@example.com"
    assert body["is_verified"] is True
    assert "password_hash" not in body


async def test_me_missing_auth_header_rejected(client):
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 401


async def test_refresh_returns_new_token_with_fresh_expiry(client, email_sender, app):
    await _full_registration(client, email_sender, "rf@example.com", "correct-horse-8")
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": "rf@example.com", "password": "correct-horse-8"},
    )
    old = login.json()["access_token"]

    r = await client.post(
        "/api/v1/auth/refresh", headers={"Authorization": f"Bearer {old}"}
    )
    assert r.status_code == 200
    new = r.json()["access_token"]
    # iat/exp of the new token should be >= those of the old one.
    secret = app.state.settings.jwt_secret
    old_claims = jwt.decode(old, secret, algorithms=["HS256"])
    new_claims = jwt.decode(new, secret, algorithms=["HS256"])
    assert new_claims["exp"] >= old_claims["exp"]
    assert new_claims["sub"] == old_claims["sub"]


async def test_expired_access_token_rejected(client, email_sender, app):
    await _full_registration(client, email_sender, "exp@example.com", "correct-horse-8")
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": "exp@example.com", "password": "correct-horse-8"},
    )
    token = login.json()["access_token"]
    # Forge an already-expired token for the same user with the same secret.
    claims = jwt.decode(token, app.state.settings.jwt_secret, algorithms=["HS256"])
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    expired = jwt.encode(
        {**claims, "iat": int(past.timestamp()) - 60, "exp": int(past.timestamp())},
        app.state.settings.jwt_secret,
        algorithm="HS256",
    )
    r = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"}
    )
    assert r.status_code == 401
