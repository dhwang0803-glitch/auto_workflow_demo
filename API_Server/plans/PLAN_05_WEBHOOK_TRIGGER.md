# PLAN_05 — Webhook 수신 트리거 (API_Server)

> **브랜치**: `API_Server` · **작성일**: 2026-04-16 · **상태**: Draft
>
> 외부 시스템(GitHub, Slack, Stripe 등)이 HTTP POST 로 워크플로우
> 실행을 트리거할 수 있는 동적 Webhook 엔드포인트를 제공한다.
> Database 의 `WebhookRegistry` (register/resolve/unregister) 위에
> HMAC 서명 검증 + 실행 트리거를 얹는다.

## 1. 목표

1. `POST /api/v1/workflows/{id}/webhook` — webhook 경로 등록 (secret 자동 생성)
2. `DELETE /api/v1/workflows/{id}/webhook` — webhook 경로 해제
3. `POST /webhooks/{path}` — 외부 트리거 수신 (HMAC-SHA256 서명 검증 + 실행 생성)
4. `main.py` lifespan 에 `PostgresWebhookRegistry` 주입

## 2. 범위

**In**
- `app/routers/webhooks.py` 신규 — 외부 수신 라우터 (인증 없음, HMAC 검증)
- `app/routers/workflows.py` 확장 — webhook 등록/해제 엔드포인트
- `app/services/workflow_service.py` 확장 — `register_webhook`, `unregister_webhook`
- `app/models/webhook.py` 신규 — `WebhookResponse`
- `app/main.py` 확장 — `PostgresWebhookRegistry` lifespan 주입 + webhooks 라우터 등록
- `tests/test_webhooks.py` 신규

**Out**
- Agent 관리 (WebSocket) — PLAN_06
- Webhook retry / dead letter — Phase 2
- Rate limiting — Phase 2
- Webhook payload 변환 (외부 → 내부 포맷) — Phase 2

## 3. 엔드포인트

| 메서드 | 경로 | 인증 | 설명 | 응답 |
|--------|------|------|------|------|
| `POST` | `/api/v1/workflows/{id}/webhook` | JWT | webhook 등록 | 201 `WebhookResponse` |
| `DELETE` | `/api/v1/workflows/{id}/webhook` | JWT | webhook 해제 | 204 |
| `POST` | `/webhooks/{path}` | HMAC | 외부 트리거 수신 | 202 `{"execution_id": "..."}` |

**에러 코드**:

| 상황 | HTTP |
|------|------|
| 워크플로우 미존재 / 소유권 없음 | 404 |
| 비활성 워크플로우에 webhook 등록 | 409 |
| webhook 경로 미존재 (`/webhooks/{path}`) | 404 |
| HMAC 서명 불일치 또는 누락 | 401 |
| 워크플로우 비활성 상태에서 트리거 수신 | 409 |

## 4. HMAC 서명 검증

외부 요청의 `X-Webhook-Signature` 헤더를 검증:

```
expected = HMAC-SHA256(secret, request_body)
actual = request.headers["X-Webhook-Signature"]
```

- `secrets.token_urlsafe(32)` 로 secret 자동 생성 (등록 시)
- 검증은 `hmac.compare_digest` 사용 (timing attack 방지)
- 서명 없거나 불일치 → 401

## 5. 서비스 로직

### register_webhook(user, workflow_id)
1. 소유권 + is_active 확인
2. `secret = secrets.token_urlsafe(32)`
3. `webhook_registry.register(workflow_id, secret=secret)` → `WebhookBinding` 반환
4. return binding (path + secret, 등록 시에만 secret 노출)

### unregister_webhook(user, workflow_id)
1. 소유권 확인
2. `webhook_registry` 에서 해당 workflow 의 바인딩 조회 → 없으면 무시 (멱등)
3. `webhook_registry.unregister(path)`

### receive_webhook(path, body, signature)
1. `webhook_registry.resolve(path)` → 없으면 404
2. HMAC 검증 → 실패 시 401
3. `workflow_repo.get(binding.workflow_id)` → 비활성이면 409
4. `user_repo.get(workflow.owner_id)` → 워크플로우 소유자로 실행
5. `execute_workflow(user, workflow_id)` → 202 + execution_id

## 6. Pydantic 스키마 (`app/models/webhook.py`)

```python
class WebhookResponse(BaseModel):
    path: str
    secret: str
    workflow_id: UUID
    created_at: datetime | None = None
```

## 7. 함수 증식 방지 가드레일

- `WorkflowService` 에 메서드 2개 추가 (`register_webhook`, `unregister_webhook`)
- 외부 수신 로직 (`receive_webhook`) 도 `WorkflowService` 에 — 별도 `WebhookService` 금지
- HMAC 검증은 `receive_webhook` 본문에서 3줄 인라인. `_verify_hmac` 헬퍼 금지
- 라우터에 try/except 0개

## 8. 테스트

1. `test_register_webhook_happy` — 201 + path/secret 반환
2. `test_register_webhook_not_owned_404`
3. `test_register_webhook_inactive_409`
4. `test_unregister_webhook_happy` — 204
5. `test_unregister_webhook_idempotent` — 이미 없어도 204
6. `test_receive_webhook_happy` — 올바른 서명 → 202 + execution_id
7. `test_receive_webhook_bad_signature_401`
8. `test_receive_webhook_unknown_path_404`

## 9. 수용 기준

- [ ] 신규 8 테스트 통과
- [ ] 기존 50 테스트 회귀 없음 (총 58+)
- [ ] HMAC 검증에 `hmac.compare_digest` 사용
- [ ] webhook secret 은 등록 응답에만 포함, 이후 조회 불가
- [ ] `WorkflowService` 에 1회용 private 헬퍼 0개
- [ ] 라우터에 try/except 0개

## 10. 후속 영향

- **PLAN_06 (Agent 관리)** — 마지막 API_Server PLAN. WebSocket 등록/heartbeat
- **Frontend** — 워크플로우 설정에서 "Webhook URL" 복사 버튼 + secret 표시 (등록 시 1회)
- **Phase 2** — retry, dead letter, payload transform, rate limit

## 11. 작업 순서

1. PLAN_05 문서 (본 문서) ✓
2. `app/models/webhook.py` 신규
3. `app/services/workflow_service.py` 확장 — webhook_registry 주입 + 메서드 3개
4. `app/routers/webhooks.py` 신규 — 외부 수신
5. `app/routers/workflows.py` 확장 — 등록/해제
6. `app/main.py` 확장 — WebhookRegistry lifespan + 라우터
7. `tests/test_webhooks.py` 작성
8. 테스트 통과 확인
9. PR 생성 → 리뷰 → 머지
