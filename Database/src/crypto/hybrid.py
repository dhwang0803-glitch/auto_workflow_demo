"""Hybrid RSA-AES credential transport — ADR-013.

Server wraps a fresh AES-256 key with the Agent's RSA-2048 public key
(OAEP-SHA256) and encrypts the credential payload with AES-256-GCM. The
Agent unwraps the AES key with its private key and decrypts the payload.

Pure RSA is not an option: Fernet-decrypted credential plaintext routinely
exceeds the 190-byte single-block limit of RSA-2048 OAEP-SHA256.

This module is the single source of truth for the wire format. Both
`FernetCredentialStore.retrieve_for_agent` and the InMemory test double
call `hybrid_encrypt` here so any regression shows up in one place.
"""
from __future__ import annotations

import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from Database.src.repositories.base import AgentCredentialPayload

_AES_KEY_BYTES = 32   # AES-256
_GCM_NONCE_BYTES = 12
_OAEP = padding.OAEP(
    mgf=padding.MGF1(algorithm=hashes.SHA256()),
    algorithm=hashes.SHA256(),
    label=None,
)


def hybrid_encrypt(
    plaintext: bytes, agent_public_key_pem: bytes
) -> AgentCredentialPayload:
    public_key = serialization.load_pem_public_key(agent_public_key_pem)
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise ValueError("agent public key must be RSA")

    aes_key = os.urandom(_AES_KEY_BYTES)
    nonce = os.urandom(_GCM_NONCE_BYTES)
    ciphertext = AESGCM(aes_key).encrypt(nonce, plaintext, None)
    wrapped_key = public_key.encrypt(aes_key, _OAEP)

    return AgentCredentialPayload(
        wrapped_key=wrapped_key,
        nonce=nonce,
        ciphertext=ciphertext,
    )


def hybrid_decrypt(
    payload: AgentCredentialPayload, agent_private_key_pem: bytes
) -> bytes:
    """Agent-side counterpart. Used by tests to verify round-trip.

    Production server code never calls this — private keys live only on the
    Agent. Kept here so the spec has a single executable reference.
    """
    private_key = serialization.load_pem_private_key(
        agent_private_key_pem, password=None
    )
    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise ValueError("agent private key must be RSA")

    aes_key = private_key.decrypt(payload.wrapped_key, _OAEP)
    return AESGCM(aes_key).decrypt(payload.nonce, payload.ciphertext, None)
