# PLAN_05 — Webhook trigger intake (API_Server)

> **Branch**: `API_Server` · **Drafted**: 2026-04-16 · **Status**: Draft
>
> Provides dynamic Webhook endpoints that let external systems (GitHub,
> Slack, Stripe, etc.) trigger workflow execution over HTTP POST. Layers
> HMAC signature verification + execution triggering on top of Database's
> `WebhookRegistry` (register/resolve/unregister).

## 1. Goals

1. `POST /api/v1/workflows/{id}/webhook` — register a webhook path (auto-generated secret)
2. `DELETE /api/v1/workflows/{id}/webhook` — unregister the webhook path
3. `POST /webhooks/{path}` — accept external triggers (HMAC-SHA256 signature verification + execution creation)
4. Inject `PostgresWebhookRegistry` in `main.py` lifespan

## 2. Scope

**In**
- `app/routers/webhooks.py` (new) — external-intake router (no auth, HMAC verified)
- `app/routers/workflows.py` extension — webhook register/unregister endpoints
- `app/services/workflow_service.py` extension — `register_webhook`, `unregister_webhook`
- `app/models/webhook.py` (new) — `WebhookResponse`
- `app/main.py` extension — `PostgresWebhookRegistry` lifespan injection + webhooks router registration
- `tests/test_webhooks.py` (new)

**Out**
- Agent management (WebSocket) — PLAN_06
- Webhook retry / dead letter — Phase 2
- Rate limiting — Phase 2
- Webhook payload transformation (external → internal format) — Phase 2

## 3. Endpoints

| Method | Path | Auth | Description | Response |
|--------|------|------|-------------|----------|
| `POST` | `/api/v1/workflows/{id}/webhook` | JWT | Register webhook | 201 `WebhookResponse` |
| `DELETE` | `/api/v1/workflows/{id}/webhook` | JWT | Unregister webhook | 204 |
| `POST` | `/webhooks/{path}` | HMAC | Receive external trigger | 202 `{"execution_id": "..."}` |

**Error codes**:

| Condition | HTTP |
|-----------|------|
| Workflow missing / no ownership | 404 |
| Webhook registration on an inactive workflow | 409 |
| Webhook path missing (`/webhooks/{path}`) | 404 |
| HMAC signature mismatched or missing | 401 |
| Trigger received while workflow is inactive | 409 |

## 4. HMAC signature verification

Verify the external request's `X-Webhook-Signature` header:

```
expected = HMAC-SHA256(secret, request_body)
actual = request.headers["X-Webhook-Signature"]
```

- Auto-generate the secret via `secrets.token_urlsafe(32)` on registration
- Use `hmac.compare_digest` for verification (timing-attack defense)
- Missing or mismatched signature → 401

## 5. Service logic

### register_webhook(user, workflow_id)
1. Verify ownership + is_active
2. `secret = secrets.token_urlsafe(32)`
3. `webhook_registry.register(workflow_id, secret=secret)` → returns `WebhookBinding`
4. Return the binding (path + secret, secret exposed only at registration time)

### unregister_webhook(user, workflow_id)
1. Verify ownership
2. Look up the workflow's binding in `webhook_registry` — if absent, no-op (idempotent)
3. `webhook_registry.unregister(path)`

### receive_webhook(path, body, signature)
1. `webhook_registry.resolve(path)` → 404 if missing
2. Verify HMAC → 401 on failure
3. `workflow_repo.get(binding.workflow_id)` → 409 if inactive
4. `user_repo.get(workflow.owner_id)` → execute as the workflow owner
5. `execute_workflow(user, workflow_id)` → 202 + execution_id

## 6. Pydantic schema (`app/models/webhook.py`)

```python
class WebhookResponse(BaseModel):
    path: str
    secret: str
    workflow_id: UUID
    created_at: datetime | None = None
```

## 7. Function-sprawl-prevention guardrails

- Add 2 methods to `WorkflowService` (`register_webhook`, `unregister_webhook`)
- External intake (`receive_webhook`) also goes on `WorkflowService` — no separate `WebhookService`
- HMAC verification is 3 inline lines inside `receive_webhook`. No `_verify_hmac` helper
- 0 try/except in the router

## 8. Tests

1. `test_register_webhook_happy` — 201 + path/secret returned
2. `test_register_webhook_not_owned_404`
3. `test_register_webhook_inactive_409`
4. `test_unregister_webhook_happy` — 204
5. `test_unregister_webhook_idempotent` — already absent → still 204
6. `test_receive_webhook_happy` — correct signature → 202 + execution_id
7. `test_receive_webhook_bad_signature_401`
8. `test_receive_webhook_unknown_path_404`

## 9. Acceptance criteria

- [ ] The 8 new tests pass
- [ ] No regression in the existing 50 tests (total 58+)
- [ ] HMAC verification uses `hmac.compare_digest`
- [ ] Webhook secret appears only in the registration response — not retrievable afterward
- [ ] 0 single-use private helpers in `WorkflowService`
- [ ] 0 try/except in the router

## 10. Downstream impact

- **PLAN_06 (Agent management)** — last API_Server PLAN. WebSocket register/heartbeat
- **Frontend** — in workflow settings, a "Webhook URL" copy button + secret display (once on registration)
- **Phase 2** — retry, dead letter, payload transform, rate limit

## 11. Work order

1. Write the PLAN_05 document (this document) ✓
2. New `app/models/webhook.py`
3. Extend `app/services/workflow_service.py` — inject webhook_registry + 3 methods
4. New `app/routers/webhooks.py` — external intake
5. Extend `app/routers/workflows.py` — register/unregister
6. Extend `app/main.py` — WebhookRegistry lifespan + router
7. Write `tests/test_webhooks.py`
8. Verify tests pass
9. Open PR → review → merge
