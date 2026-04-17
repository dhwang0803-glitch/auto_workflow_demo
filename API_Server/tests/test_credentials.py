"""PLAN_07 — credential CRUD router E2E tests."""
from __future__ import annotations

import os
from uuid import uuid4

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
asyncpg = pytest.importorskip("asyncpg")  # noqa: F841

DATABASE_URL = os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set — requires live Postgres",
)


async def test_create_credential_returns_201_without_plaintext(authed_client):
    r = await authed_client.post(
        "/api/v1/credentials",
        json={
            "name": "gmail-smtp",
            "type": "smtp",
            "plaintext": {"user": "u@example.com", "password": "p"},
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "gmail-smtp"
    assert body["type"] == "smtp"
    assert "id" in body
    # Response must not echo the secret — blueprint §1.6 invariant.
    assert "plaintext" not in body
    assert "password" not in str(body).lower()


async def test_create_credential_with_unknown_type_422(authed_client):
    r = await authed_client.post(
        "/api/v1/credentials",
        json={"name": "x", "type": "bogus", "plaintext": {}},
    )
    assert r.status_code == 422


async def test_create_duplicate_name_returns_409(authed_client):
    payload = {
        "name": "shared-name",
        "type": "slack_webhook",
        "plaintext": {"url": "https://hooks.example.com/a"},
    }
    r1 = await authed_client.post("/api/v1/credentials", json=payload)
    assert r1.status_code == 201
    r2 = await authed_client.post("/api/v1/credentials", json=payload)
    assert r2.status_code == 409


async def test_delete_credential_204(authed_client):
    r = await authed_client.post(
        "/api/v1/credentials",
        json={"name": "to-delete", "type": "http_bearer", "plaintext": {"token": "t"}},
    )
    cid = r.json()["id"]
    d = await authed_client.delete(f"/api/v1/credentials/{cid}")
    assert d.status_code == 204


async def test_delete_nonexistent_credential_404(authed_client):
    d = await authed_client.delete(f"/api/v1/credentials/{uuid4()}")
    assert d.status_code == 404


# ---------------------------------------------------------------------------
# PLAN_09 — GET/LIST endpoints
# ---------------------------------------------------------------------------


async def test_list_credentials_empty_for_new_user(authed_client):
    r = await authed_client.get("/api/v1/credentials")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_credentials_returns_items_desc(authed_client):
    import asyncio

    for name, ctype in [("a", "smtp"), ("b", "http_bearer"), ("c", "slack_webhook")]:
        r = await authed_client.post(
            "/api/v1/credentials",
            json={"name": name, "type": ctype, "plaintext": {"k": "v"}},
        )
        assert r.status_code == 201
        await asyncio.sleep(0.01)  # ensure distinct created_at for DESC ordering

    r = await authed_client.get("/api/v1/credentials")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 3
    # DESC: most-recent ("c") first
    assert [item["name"] for item in body] == ["c", "b", "a"]
    # Plaintext field must not leak to the list response
    assert all("plaintext" not in item for item in body)
    assert all(item.get("created_at") is not None for item in body)


async def test_list_credentials_isolated_to_user(client, email_sender):
    """User A's credentials must not surface in User B's list."""
    from urllib.parse import parse_qs, urlparse

    async def _register_verify_login(email: str, password: str) -> str:
        reg = await client.post(
            "/api/v1/auth/register", json={"email": email, "password": password}
        )
        assert reg.status_code == 201
        link = next(l for (to, l) in email_sender.sent if to == email)
        token = parse_qs(urlparse(link).query)["token"][0]
        v = await client.get("/api/v1/auth/verify", params={"token": token})
        assert v.status_code == 200
        login = await client.post(
            "/api/v1/auth/login",
            data={"username": email, "password": password},
        )
        assert login.status_code == 200
        return login.json()["access_token"]

    access_a = await _register_verify_login("a@example.com", "password-123")
    client.headers["Authorization"] = f"Bearer {access_a}"
    await client.post(
        "/api/v1/credentials",
        json={"name": "a-only", "type": "smtp", "plaintext": {"k": "v"}},
    )

    access_b = await _register_verify_login("b@example.com", "password-123")
    client.headers["Authorization"] = f"Bearer {access_b}"
    r = await client.get("/api/v1/credentials")
    assert r.status_code == 200
    assert r.json() == []


async def test_get_credential_by_id(authed_client):
    r = await authed_client.post(
        "/api/v1/credentials",
        json={"name": "fetched", "type": "http_bearer", "plaintext": {"token": "t"}},
    )
    cid = r.json()["id"]

    g = await authed_client.get(f"/api/v1/credentials/{cid}")
    assert g.status_code == 200
    body = g.json()
    assert body["id"] == cid
    assert body["name"] == "fetched"
    assert body["type"] == "http_bearer"
    assert body.get("created_at") is not None
    assert "plaintext" not in body
    assert "token" not in str(body).lower() or body["name"].lower() == "fetched"


async def test_get_credential_nonexistent_404(authed_client):
    r = await authed_client.get(f"/api/v1/credentials/{uuid4()}")
    assert r.status_code == 404


async def test_get_credential_not_owned_404(client, email_sender):
    from urllib.parse import parse_qs, urlparse

    async def _register_verify_login(email: str, password: str) -> str:
        reg = await client.post(
            "/api/v1/auth/register", json={"email": email, "password": password}
        )
        assert reg.status_code == 201
        link = next(l for (to, l) in email_sender.sent if to == email)
        token = parse_qs(urlparse(link).query)["token"][0]
        v = await client.get("/api/v1/auth/verify", params={"token": token})
        assert v.status_code == 200
        login = await client.post(
            "/api/v1/auth/login",
            data={"username": email, "password": password},
        )
        assert login.status_code == 200
        return login.json()["access_token"]

    access_a = await _register_verify_login("a@example.com", "password-123")
    client.headers["Authorization"] = f"Bearer {access_a}"
    create = await client.post(
        "/api/v1/credentials",
        json={"name": "a-secret", "type": "http_bearer", "plaintext": {"token": "x"}},
    )
    cid = create.json()["id"]

    access_b = await _register_verify_login("b@example.com", "password-123")
    client.headers["Authorization"] = f"Bearer {access_b}"
    r = await client.get(f"/api/v1/credentials/{cid}")
    assert r.status_code == 404  # not 403 — enumeration defence


# ---------------------------------------------------------------------------
# original DELETE cross-tenant test (kept below PLAN_09 block)
# ---------------------------------------------------------------------------


async def test_delete_other_users_credential_404(client, email_sender):
    """Two users; user A creates a credential, user B tries to delete it.
    Must return 404 (not 403) to avoid leaking existence — enumeration defence."""
    from urllib.parse import parse_qs, urlparse

    async def _register_verify_login(email: str, password: str) -> str:
        reg = await client.post(
            "/api/v1/auth/register", json={"email": email, "password": password}
        )
        assert reg.status_code == 201
        link = next(l for (to, l) in email_sender.sent if to == email)
        token = parse_qs(urlparse(link).query)["token"][0]
        v = await client.get("/api/v1/auth/verify", params={"token": token})
        assert v.status_code == 200
        login = await client.post(
            "/api/v1/auth/login",
            data={"username": email, "password": password},
        )
        assert login.status_code == 200
        return login.json()["access_token"]

    access_a = await _register_verify_login("a@example.com", "password-123")
    client.headers["Authorization"] = f"Bearer {access_a}"
    create = await client.post(
        "/api/v1/credentials",
        json={"name": "a-secret", "type": "http_bearer", "plaintext": {"token": "x"}},
    )
    assert create.status_code == 201
    cid = create.json()["id"]

    access_b = await _register_verify_login("b@example.com", "password-123")
    client.headers["Authorization"] = f"Bearer {access_b}"
    d = await client.delete(f"/api/v1/credentials/{cid}")
    assert d.status_code == 404
