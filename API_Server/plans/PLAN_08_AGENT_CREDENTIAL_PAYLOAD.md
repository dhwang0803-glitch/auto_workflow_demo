# PLAN_08 — Attach `credential_payloads` to the Agent execute WS (API_Server side)

> Predecessor: PLAN_07 (`/credentials` CRUD + serverless validation), Database bulk_retrieve + retrieve_for_agent
> Follow-up: Execution_Engine follow-up — Agent-daemon-side decryption + reuse of `resolve_credential_refs`

## Purpose

Activate the Heavy segment. On the Serverless path, the Worker decrypts
directly from the DB via `CredentialStore` (PLAN_08 EE). On the Agent path,
the customer's VPC can't reach the DB → the server **re-encrypts per-node
credentials with the Agent's public key** (ADR-013 hybrid) and attaches them
to the execute WS message. The Agent decrypts with its private key, then
reuses the same `resolve_credential_refs`.

## File changes

### New
| File | Role |
|------|------|
| `tests/test_agent_credential_payload.py` | E2E verifies the `credential_payloads` field on the WS execute message |

### Modified
| File | Change |
|------|--------|
| `app/services/workflow_service.py` | Inject `credential_store` into the constructor; in the `execute_workflow` agent branch, loop `retrieve_for_agent` → base64 → append `credential_payloads`. Hoist ref_id collection to the top of the function so validation and the agent payload share the same variable. |
| `app/container.py` | Wire `WorkflowService(credential_store=self.credential_store, ...)` |

### Out of scope
- Agent-daemon decryption / resolve (Execution_Engine follow-up PR)
- A state where `credential_payloads` is present but the store isn't configured — the container composes them with the same master_key, so this can't happen in a healthy deployment (defensive code is kept minimal)

## Implementation details

### `WorkflowService` constructor extension

```python
def __init__(
    self,
    *,
    ...
    credential_service: CredentialService | None = None,
    credential_store: CredentialStore | None = None,   # NEW
) -> None:
    ...
    self._credential_store = credential_store
```

### `execute_workflow` restructure

```python
async def execute_workflow(self, user, workflow_id) -> Execution:
    wf = ...
    ...

    # Collect credential_ref ids once — used for validation AND (agent mode) payload build.
    ref_ids: list[UUID] = []
    for node in wf.graph.get("nodes", []):
        ref = (node.get("config") or {}).get("credential_ref")
        if ref and "credential_id" in ref:
            ref_ids.append(UUID(ref["credential_id"]))

    if ref_ids and self._credential_service is not None:
        await self._credential_service.validate_refs(user, ref_ids)

    execution = Execution(...)
    await self._exec_repo.create(execution)

    if execution.execution_mode == "serverless" and ...:
        # unchanged — Worker handles credential resolution
        ...
    elif execution.execution_mode == "agent" and self._agent_repo:
        agents = await self._agent_repo.list_by_owner(user.id)
        dispatched = False
        for ag in agents:
            ws = self._agent_connections.get(ag.id)
            if ws is not None:
                credential_payloads = []
                if ref_ids and self._credential_store is not None:
                    for cid in ref_ids:
                        envelope = await self._credential_store.retrieve_for_agent(
                            cid, agent_public_key_pem=ag.public_key.encode("utf-8"),
                        )
                        credential_payloads.append({
                            "credential_id": str(cid),
                            "wrapped_key": base64.b64encode(envelope.wrapped_key).decode(),
                            "nonce": base64.b64encode(envelope.nonce).decode(),
                            "ciphertext": base64.b64encode(envelope.ciphertext).decode(),
                        })
                await ws.send_json({
                    "type": "execute",
                    "execution_id": str(execution.id),
                    "workflow_id": str(wf.id),
                    "graph": wf.graph,
                    "credential_payloads": credential_payloads,
                })
                dispatched = True
                break
        if not dispatched:
            await self._exec_repo.update_status(
                execution.id, "failed",
                error={"message": "no connected agent"},
            )

    return execution
```

## Security invariants

- `retrieve_for_agent` is the ADR-013 path — the server sees plaintext momentarily, then immediately re-encrypts with the Agent's public key
- The WS message carries the envelope encrypted with the Agent's public key (not already-decrypted plaintext) → safe on the wire
- Even with an empty `credential_payloads` array, if the Agent fails on a graph that has credential_refs it's safe (the Agent self-validates — EE follow-up)

## Test strategy

### test_agent_credential_payload.py (E2E, requires DATABASE_URL)

During the test, generate an RSA keypair dynamically (cryptography library).
Use a fresh keypair instead of the hardcoded `RSA_PUB_KEY` from the existing
`test_agents.py`.

1. `test_execute_agent_includes_credential_payloads` — register a credential + workflow (credential_ref) + agent connection → execute → the WS execute message contains `credential_payloads` of length 1, each field is base64-decodable
2. `test_execute_agent_no_refs_sends_empty_payloads` — workflow with no refs → `credential_payloads=[]`
3. `test_execute_agent_multiple_refs_each_payload_distinct` — 2 credentials → 2 payloads with distinct `credential_id`s

## Checklist

- [ ] `workflow_service.py` — inject `credential_store` + add `credential_payloads` to the agent branch of `execute_workflow`
- [ ] `container.py` — wiring
- [ ] 3 tests (Docker Postgres required)
- [ ] No regression in the existing 72 tests
- [ ] Commit → push → PR

## Out of scope

- Agent-daemon decryption (next PR, Execution_Engine)
- Defensive "failed" when `credential_payloads` exists but the store doesn't — the container composes them together, so defense isn't needed, only a log warning
- Credential expiry mid-Agent-reconnect — follow-up
