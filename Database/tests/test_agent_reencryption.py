"""PLAN_05 — Agent hybrid credential re-encryption tests.

Verifies the ADR-013 wire format end-to-end: store credential → Fernet
encrypt-at-rest → re-wrap for Agent → Agent-side decrypt roundtrips to
the original plaintext. Also asserts tamper detection and wrong-key
rejection so regressions in either crypto layer show up here.

Integration tests against Postgres are gated on `DATABASE_URL`; the
pure-crypto and InMemory paths run unconditionally.
"""
from __future__ import annotations

import json
import os
from dataclasses import replace
from uuid import uuid4

import pytest

cryptography = pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from Database.src.crypto.hybrid import hybrid_decrypt, hybrid_encrypt
from Database.src.repositories.base import AgentCredentialPayload
from Database.tests.fakes import InMemoryCredentialStore


# --------------------------------------------------------------------- helpers


def _make_rsa_keypair(bits: int = 2048) -> tuple[bytes, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    priv_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv_pem, pub_pem


# ----------------------------------------------------------------- pure crypto


def test_hybrid_roundtrip_restores_plaintext():
    priv, pub = _make_rsa_keypair()
    plaintext = b'{"api_key": "sk-live-xxxxxxxxxxxxxxxxxxxxxxxx"}'

    payload = hybrid_encrypt(plaintext, pub)

    assert isinstance(payload, AgentCredentialPayload)
    assert len(payload.wrapped_key) == 256  # RSA-2048 fixed
    assert len(payload.nonce) == 12  # GCM nonce
    assert len(payload.ciphertext) == len(plaintext) + 16  # + GCM tag
    assert hybrid_decrypt(payload, priv) == plaintext


def test_hybrid_large_payload_over_oaep_block_limit():
    # 2 KB plaintext deliberately exceeds RSA-2048 OAEP-SHA256 single-block
    # limit (190 B). The hybrid scheme is only justified if this works.
    priv, pub = _make_rsa_keypair()
    plaintext = (b"A" * 2048)

    payload = hybrid_encrypt(plaintext, pub)

    assert hybrid_decrypt(payload, priv) == plaintext


def test_hybrid_tampered_ciphertext_rejected():
    priv, pub = _make_rsa_keypair()
    payload = hybrid_encrypt(b"secret-value", pub)

    flipped = bytearray(payload.ciphertext)
    flipped[0] ^= 0x01
    tampered = replace(payload, ciphertext=bytes(flipped))

    with pytest.raises(Exception):  # InvalidTag
        hybrid_decrypt(tampered, priv)


def test_hybrid_wrong_private_key_rejected():
    _, pub = _make_rsa_keypair()
    wrong_priv, _ = _make_rsa_keypair()
    payload = hybrid_encrypt(b"secret-value", pub)

    with pytest.raises(Exception):
        hybrid_decrypt(payload, wrong_priv)


def test_hybrid_rejects_non_rsa_public_key():
    from cryptography.hazmat.primitives.asymmetric import ed25519

    bad = ed25519.Ed25519PrivateKey.generate().public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    with pytest.raises(ValueError, match="RSA"):
        hybrid_encrypt(b"x", bad)


# --------------------------------------------------------------- InMemory path


@pytest.mark.asyncio
async def test_inmemory_retrieve_for_agent_roundtrip():
    store = InMemoryCredentialStore()
    priv, pub = _make_rsa_keypair()
    plaintext = {"api_key": "sk-live-12345", "endpoint": "https://api.example.com"}

    cid = await store.store(uuid4(), "openai", plaintext)
    payload = await store.retrieve_for_agent(cid, agent_public_key_pem=pub)

    decrypted_bytes = hybrid_decrypt(payload, priv)
    assert json.loads(decrypted_bytes.decode("utf-8")) == plaintext


# --------------------------------------------------------------- Postgres path

DATABASE_URL = os.getenv("DATABASE_URL")

pytestmark_pg = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set — Postgres integration test requires live DB",
)


@pytestmark_pg
@pytest.mark.asyncio
async def test_fernet_store_retrieve_for_agent_roundtrip():
    pytest.importorskip("sqlalchemy")
    pytest.importorskip("asyncpg")

    from cryptography.fernet import Fernet

    from Database.src.models.core import User as UserORM
    from Database.src.repositories._session import (
        build_engine,
        build_sessionmaker,
    )
    from Database.src.repositories.credential_store import FernetCredentialStore

    engine = build_engine(DATABASE_URL)
    sm = build_sessionmaker(engine)
    store = FernetCredentialStore(sm, master_key=Fernet.generate_key())
    priv, pub = _make_rsa_keypair()
    plaintext = {"token": "ghp_abcdef0123456789", "scopes": ["repo", "workflow"]}

    try:
        async with sm() as s, s.begin():
            user = UserORM(email=f"{uuid4()}@test.local", plan_tier="heavy")
            s.add(user)
            await s.flush()
            owner_id = user.id

        cid = await store.store(owner_id, "github", plaintext)
        try:
            payload = await store.retrieve_for_agent(
                cid, agent_public_key_pem=pub
            )
            assert len(payload.wrapped_key) == 256
            decrypted = hybrid_decrypt(payload, priv)
            assert json.loads(decrypted.decode("utf-8")) == plaintext
        finally:
            await store.delete(cid)
    finally:
        await engine.dispose()
