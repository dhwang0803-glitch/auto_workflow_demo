# PLAN_10 — Agent daemon credential decrypt + inject

> Predecessor: API_Server PLAN_08 (PR #52, merged) — bundles `credential_payloads` into the execute WS message
> Reference ADRs: ADR-013 (hybrid transport), ADR-016 (pipeline deferral)
> Then: docs branch PR — document the Agent WS protocol fields in detail (bundled with this PR)

## Purpose

The Agent daemon decrypts the `credential_payloads` received from the
server using the **RSA private key inside the VPC** and, right before
workflow execution, injects plaintext into config via
`resolve_credential_refs`. This closes the credential pipeline
end-to-end for the Heavy-segment Agent path.

## File changes

### New
| File | Role |
|------|------|
| `src/agent/credential_client.py` | `decrypt_payloads(payloads, private_key)` + `PreDecryptedCredentialStore` (CredentialStore wrapper) |
| `tests/test_agent_credentials.py` | PreDecryptedStore + decrypt_payloads units + handle_execute E2E |

### Modified
| File | Change |
|------|--------|
| `src/agent/command_handler.py` | `handle_execute` accepts `agent_private_key_pem` kwarg. If `credential_payloads` is present, decrypt → resolve graph via `resolve_credential_refs`. On failure, generic `failed`. |
| `src/agent/main.py` | `run_agent` accepts `agent_private_key_pem` and passes it through to the execute-message routing |
| `scripts/agent_run.py` | `--agent-private-key <PEM path>` CLI arg. Missing file → None — graphs with credential_ref fail. |

### Out of scope
- Automating Agent private-key generation / provisioning (currently manual keypair generation + public-key registration)
- Per-payload failure handling for `credential_payloads` (all succeed or all fail)
- docs/context updates (separate docs PR — covers the field names/flow of API_Server PLAN_08 and this PR at once)

## Implementation details

### 1. `credential_client.py` — decrypt + PreDecryptedStore

```python
def decrypt_payloads(payloads, private_key_pem) -> dict[UUID, dict]:
    """Plaintext dict map keyed by credential_id."""
    out: dict[UUID, dict] = {}
    for p in payloads:
        envelope = AgentCredentialPayload(
            wrapped_key=b64dec(p["wrapped_key"]),
            nonce=b64dec(p["nonce"]),
            ciphertext=b64dec(p["ciphertext"]),
        )
        plaintext = hybrid_decrypt(envelope, private_key_pem)
        out[UUID(p["credential_id"])] = json.loads(plaintext.decode("utf-8"))
    return out


class PreDecryptedCredentialStore(CredentialStore):
    """Agent-side CredentialStore implementation. Ignores the owner_id filter
    (the server already validated it). For resolve_credential_refs compatibility."""
    ...
```

- `PreDecryptedCredentialStore.bulk_retrieve` accepts owner_id but ignores it —
  document this in the docstring. The server already filtered by ownership when
  issuing the credentials, so any payload the Agent received is, by definition,
  owned by that user.
- The other ABC methods (`store`, `retrieve`, `delete`, `retrieve_for_agent`) raise `NotImplementedError`.

### 2. `command_handler.handle_execute` extension

```python
async def handle_execute(
    ws,
    msg,
    node_registry,
    *,
    agent_private_key_pem: bytes | None = None,
) -> None:
    execution_id = msg["execution_id"]
    graph = msg["graph"]
    execution = Execution(...)
    ws_repo = WebSocketExecutionRepository(ws, execution)

    if graph_has_credential_refs(graph):
        payloads = msg.get("credential_payloads") or []
        if not payloads or agent_private_key_pem is None:
            await ws_repo.update_status(
                execution.id, "failed",
                error={"message": "credential resolution failed"},
            )
            return
        try:
            decrypted = decrypt_payloads(payloads, agent_private_key_pem)
            store = PreDecryptedCredentialStore(decrypted)
            # owner_id is ignored by PreDecryptedStore → pass a dummy
            graph = await resolve_credential_refs(graph, store, owner_id=uuid4())
        except Exception:
            await ws_repo.update_status(
                execution.id, "failed",
                error={"message": "credential resolution failed"},
            )
            return

    await run_workflow(graph, execution, ws_repo, node_registry)
```

- Error message matches the PLAN_08 Worker: `"credential resolution failed"` (generic) — no credential_id leakage.

### 3. `main.py` + `agent_run.py` wiring

```python
# scripts/agent_run.py
parser.add_argument("--agent-private-key", default=None,
                    help="PEM file with RSA private key (Agent-owned)")
...
private_key_pem = None
if args.agent_private_key:
    with open(args.agent_private_key, "rb") as f:
        private_key_pem = f.read()

asyncio.run(run_agent(
    ..., agent_private_key_pem=private_key_pem,
))
```

### 4. Security invariants

- The private-key file path lives on the customer-VPC filesystem — not exposed outside the Agent.
- `hybrid_decrypt` failures (wrong key, tampered ciphertext) → propagate `cryptography.exceptions.InvalidKey` / `InvalidTag` etc. → caught by try/except as generic `failed`.
- Decrypted plaintext sits in the `PreDecryptedCredentialStore` field after `decrypt_payloads` returns, but is GC'd when `handle_execute` scope ends.
- Thanks to the deep copy in `resolve_credential_refs`, the original graph stays plaintext-free (same property as the PLAN_08 Worker).

## Test strategy

### `test_agent_credentials.py`

**Unit — PreDecryptedStore + decrypt_payloads (no DB):**
1. `test_decrypt_payloads_roundtrip` — generate a test keypair → hybrid_encrypt → b64 → decrypt_payloads → restores the original
2. `test_pre_decrypted_store_bulk_retrieve` — store two credentials → bulk lookup returns dict
3. `test_pre_decrypted_store_missing_raises` — unknown id → KeyError
4. `test_pre_decrypted_store_ignores_owner_id` — works with an arbitrary owner_id (server-filter premise)

**E2E — handle_execute + fake WS (no DB):**
5. `test_handle_execute_decrypts_and_runs` — credential_payloads attached → node receives plaintext config → success
6. `test_handle_execute_no_refs_ignores_payloads` — graph without refs → run anyway even if payloads are present (regression)
7. `test_handle_execute_refs_without_private_key_fails` — refs present but private_key is None → failed + generic message
8. `test_handle_execute_refs_without_payloads_fails` — refs present but credential_payloads missing → failed

## Checklist

- [ ] `src/agent/credential_client.py`
- [ ] `src/agent/command_handler.py` — extension + credential path
- [ ] `src/agent/main.py` — inject private_key into run_agent
- [ ] `scripts/agent_run.py` — `--agent-private-key` CLI
- [ ] 8 tests pass, overall 54→62
- [ ] No regression in existing test_agent.py (handle_execute kwarg defaults to None)
- [ ] Commit → push → PR

## Out of scope

- Automating Agent key provisioning / rotation (Phase 2)
- Partial failures within `credential_payloads` (all-or-nothing)
- docs/context updates — split into a docs PR (after this PR merges)
