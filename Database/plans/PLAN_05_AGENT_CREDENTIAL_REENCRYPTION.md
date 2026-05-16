# PLAN_05 — Hybrid re-encryption transport for Agent credentials

> **Branch**: `Database` · **Drafted**: 2026-04-15 · **Completed**: 2026-04-15 · **Status**: Done
>
> Turns ADR-004's "re-encrypt with the Agent's RSA public key on Agent-mode
> transport" into a concrete spec. The algorithm / parameters / frame
> schema were already locked down in **ADR-013** (AES-256-GCM + RSA-OAEP-SHA256
> hybrid, RSA-2048). This PLAN layers that spec onto the `CredentialStore`
> ABC and implements it on both Postgres and InMemory.
>
> Open questions Q1–Q4 are already reflected in ADR-013 and the
> architecture.md §2 "Agent mode" section.

## 1. Goals

1. New `CredentialStore.retrieve_for_agent()` — Fernet decrypt → hybrid
   re-encrypt → return `AgentCredentialPayload`
2. Shared crypto helper `src/crypto/hybrid.py` — `hybrid_encrypt` / `hybrid_decrypt`.
   The InMemory double and the Postgres implementation share the same functions
3. No DB schema changes — `agents.public_key` already exists (PLAN_02)
4. Integration test: round-trip with a real RSA keypair (encrypt → decrypt → plaintext matches)

## 2. Scope

**In**
- `AgentCredentialPayload` DTO (`wrapped_key`, `nonce`, `ciphertext`)
- `CredentialStore.retrieve_for_agent` ABC + Postgres implementation + InMemory implementation
- `src/crypto/hybrid.py` — helpers built on pyca/cryptography
- Integration tests: `tests/test_agent_reencryption.py` (round-trip + tamper detection + wrong-key rejection)

**Out (follow-up / other branches)**
- **Looking up the Agent's public key** — `retrieve_for_agent` **takes** the
  public-key PEM as an argument. The caller (API_Server) is responsible for
  fetching `agents.public_key` from the DB. PLAN_05 does not depend on
  `AgentRepository`
- **WebSocket `get_credential` frame handling** — API_Server / Execution_Engine responsibility
- **Agent-side decryption** — Agent branch (using the same spec as this implementation)
- **API_Server in-process cache** — follow-up PLAN. We deliberately keep
  `retrieve_for_agent` as a pure function so a future decorator can wrap it
- **DB schema changes** — none

## 3. Crypto spec (ADR-013 summary)

| Item | Value |
|------|-------|
| Symmetric layer | AES-256-GCM (12 B nonce, 16 B tag) |
| RSA key size | 2048 bit, e=65537 |
| RSA padding | OAEP, hash=SHA-256, MGF1=SHA-256, no label |
| Library | `cryptography` (pyca) — already used by Fernet |
| Per call | A fresh random AES key + a fresh nonce (never reused) |

### 3.1 Frame structure

```python
@dataclass
class AgentCredentialPayload:
    wrapped_key: bytes   # 256 B (fixed by RSA-2048)
    nonce: bytes         #  12 B
    ciphertext: bytes    # len(plaintext) + 16 B (GCM tag)
```

Serialization is the WebSocket layer's responsibility (base64 JSON). The DTO
itself carries raw bytes.

### 3.2 `hybrid_encrypt(plaintext: bytes, agent_public_key_pem: bytes)`

```
1. os.urandom(32)               → AES-256 key
2. os.urandom(12)                → GCM nonce
3. AESGCM(key).encrypt(nonce, plaintext, None)  → ciphertext (tag included)
4. load_pem_public_key(pem)
5. public_key.encrypt(
       key,
       padding.OAEP(MGF1=SHA256, algorithm=SHA256, label=None)
   )                             → wrapped_key
6. return AgentCredentialPayload(wrapped_key, nonce, ciphertext)
```

### 3.3 `hybrid_decrypt(payload, agent_private_key_pem)` — test-only

Reproduces the Agent-side decrypt flow in a test double. Production server
code never calls this function (the private key only lives in the Agent process).

## 4. Repository changes

### 4.1 Additions to `base.py`

```python
@dataclass
class AgentCredentialPayload:
    wrapped_key: bytes
    nonce: bytes
    ciphertext: bytes


class CredentialStore(ABC):
    # ... existing store/retrieve/delete ...

    @abstractmethod
    async def retrieve_for_agent(
        self,
        credential_id: UUID,
        *,
        agent_public_key_pem: bytes,
    ) -> AgentCredentialPayload: ...
```

`agent_public_key_pem` is PEM-encoded RSA public-key bytes. The caller is
expected to fetch it from `AgentRepository` (or similar) and pass it in.

### 4.2 `FernetCredentialStore.retrieve_for_agent`

```python
async def retrieve_for_agent(
    self, credential_id, *, agent_public_key_pem
) -> AgentCredentialPayload:
    plaintext_dict = await self.retrieve(credential_id)  # Fernet decrypt
    plaintext_bytes = json.dumps(plaintext_dict).encode("utf-8")
    return hybrid_encrypt(plaintext_bytes, agent_public_key_pem)
```

- No DB/cache changes
- Fernet decryption simply calls the existing `retrieve()` → single management surface
- Pure-function shape (output determined by inputs alone) → future in-process caching can wrap it

### 4.3 `InMemoryCredentialStore.retrieve_for_agent`

Calls the same `hybrid_encrypt`. Although `InMemoryCredentialStore` doesn't
use Fernet, the **hybrid encryption path goes through the real `cryptography`
calls** so frame-schema and algorithm regressions get caught in tests.

## 5. Deliverables

| Path | Content |
|------|---------|
| `src/crypto/__init__.py` | Empty package marker |
| `src/crypto/hybrid.py` | `hybrid_encrypt` / `hybrid_decrypt` |
| `src/repositories/base.py` | `AgentCredentialPayload` + ABC signature extension |
| `src/repositories/credential_store.py` | `FernetCredentialStore.retrieve_for_agent` |
| `tests/fakes.py` | `InMemoryCredentialStore.retrieve_for_agent` |
| `tests/test_agent_reencryption.py` | round-trip / tamper detection / wrong-key rejection |

## 6. Acceptance criteria

- [x] `hybrid_encrypt` → `hybrid_decrypt` round-trip restores plaintext *(test_hybrid_roundtrip_restores_plaintext)*
- [x] `FernetCredentialStore.retrieve_for_agent` restores the original dict
      via store → re-encrypt → decrypt-with-Agent-private-key *(test_fernet_store_retrieve_for_agent_roundtrip)*
- [x] `InMemoryCredentialStore.retrieve_for_agent` behaves identically *(test_inmemory_retrieve_for_agent_roundtrip)*
- [x] Tampering one byte of the ciphertext raises on decrypt *(test_hybrid_tampered_ciphertext_rejected)*
- [x] Decrypting with the wrong keypair's private key raises *(test_hybrid_wrong_private_key_rejected)*
- [x] `wrapped_key` length = 256 B (RSA-2048 fixed-size check) *(roundtrip assertion)*
- [x] 2 KB large payload handling (exceeds the OAEP single-block limit) *(test_hybrid_large_payload_over_oaep_block_limit)*
- [x] Non-RSA public keys are rejected *(test_hybrid_rejects_non_rsa_public_key)*
- [x] The existing 20 tests stay green — full 24/24 pass *(2026-04-15)*

## 7. Open issues

1. **Public-key PEM-format validation** — when the caller passes a bad PEM,
   `load_pem_public_key` raises ValueError. Whether to wrap that as a domain
   exception belongs to API_Server's error-contract design. This PLAN
   propagates it as-is.
2. **Workflows running mid-RSA-key-rotation** — when the Agent reconnects and
   registers a new public key, subsequent `get_credential` payloads use it.
   Already-running nodes decrypt payloads they received under the old key
   (no problem). PLAN_05 needs no special handling.
3. **When to introduce the API_Server in-process cache** — when measured QPS
   exceeds 100. Decided in a follow-up PLAN.

## 8. Downstream PLAN impact

- **API_Server** — adds the `get_credential` WebSocket handler. Fetches the
  public key via `AgentRepository` → calls `CredentialStore.retrieve_for_agent`
  → composes the response frame
- **Execution_Engine / Agent** — writes decryption code matching this spec.
  Conforms to the ADR-013 frame schema
