# PLAN_06 — Agent management (API_Server)

> **Branch**: `API_Server` · **Drafted**: 2026-04-16 · **Status**: Draft
>
> Lets a Heavy-plan user register their own Agent in their VPC and keep a
> standing WebSocket connection for command delivery + heartbeat + credential
> fetches. ADR-013 hybrid re-encryption is already implemented by
> `CredentialStore.retrieve_for_agent` in Database — API_Server just hosts
> the WebSocket frame handlers.

## 1. Goals

1. `POST /api/v1/agents/register` — submit an RSA public key → receive an Agent JWT
2. `WS /api/v1/agents/ws` — long-lived WebSocket (JWT-authenticated)
3. WebSocket frames: `heartbeat`, `get_credential`
4. Inject `PostgresAgentRepository` in `main.py` lifespan

## 2. Scope

**In**
- `app/routers/agents.py` (new) — registration REST + WebSocket
- `app/models/agent.py` (new) — `AgentRegisterRequest`, `AgentRegisterResponse`
- `app/services/workflow_service.py` extension — `register_agent`
- `app/main.py` extension — AgentRepository lifespan + router registration
- `tests/test_agents.py` (new)

**Out**
- Agent → API direction execution-result push — Execution_Engine's concern
- Agent GPU routing (ADR-009) — Phase 2
- Agent unregister / delete — Phase 2
- Multi-Agent load balancing — Phase 2

## 3. Endpoints

| Method | Path | Auth | Description | Response |
|--------|------|------|-------------|----------|
| `POST` | `/api/v1/agents/register` | User JWT | Register an Agent | 201 `AgentRegisterResponse` |
| `WS` | `/api/v1/agents/ws` | Agent JWT (query param) | Long-lived connection | WebSocket frames |

### AgentRegisterRequest
```python
class AgentRegisterRequest(BaseModel):
    public_key: str       # RSA PEM
    gpu_info: dict = {}
```

### AgentRegisterResponse
```python
class AgentRegisterResponse(BaseModel):
    agent_id: UUID
    agent_token: str      # Agent-only JWT (subject=agent_id)
```

## 4. Agent JWT

Separate from the User JWT — stored as `agent:{agent_id}` in `sub`.
WebSocket connection authenticates via `?token=<agent_jwt>` query param.
Expiry: 24 hours (overridable via Settings).

## 5. WebSocket frame protocol

JSON frames in the form `{"type": "<action>", ...}`:

**Client → Server:**
- `{"type": "heartbeat"}` → refresh heartbeat, response: `{"type": "heartbeat_ack"}`
- `{"type": "get_credential", "credential_id": "<uuid>"}` → returns a re-encrypted credential

**Server → Client:**
- `{"type": "heartbeat_ack"}`
- `{"type": "credential", "payload": {"wrapped_key": "...", "nonce": "...", "ciphertext": "..."}}` (base64)
- `{"type": "error", "message": "..."}`

## 6. Function-sprawl-prevention guardrails

- Add exactly one method `register_agent` to `WorkflowService`
- Handle the WebSocket inline inside the `agents.py` router — no separate `AgentManager` class
- Re-encryption calls `CredentialStore.retrieve_for_agent()` directly — no wrapper
- Frame dispatch is a 2-line `if/elif` — no `_handle_heartbeat` / `_handle_credential` helpers

## 7. Tests

1. `test_register_agent_happy` — 201 + agent_id + agent_token
2. `test_register_agent_invalid_key_422`
3. `test_register_agent_not_authenticated_401`
4. `test_ws_heartbeat` — WebSocket connect + heartbeat → ack
5. `test_ws_invalid_token_rejected`
6. `test_ws_get_credential` — confirms the re-encrypted response

## 8. Acceptance criteria

- [ ] The 6 new tests pass
- [ ] No regression in the existing 58 tests (total 64+)
- [ ] Agent JWT is distinguishable from User JWT (`sub` prefix)
- [ ] WebSocket heartbeat updates DB `last_heartbeat`
- [ ] Calls to `retrieve_for_agent` return an RSA-re-encrypted response
