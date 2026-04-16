# PLAN_06 — Agent 관리 (API_Server)

> **브랜치**: `API_Server` · **작성일**: 2026-04-16 · **상태**: Draft
>
> Heavy 플랜 유저가 자체 VPC 의 Agent 를 등록하고, WebSocket 으로
> 상시 연결하여 명령 수신 + heartbeat + 자격증명 수신을 할 수 있게 한다.
> ADR-013 하이브리드 재암호화는 Database 의 `CredentialStore.retrieve_for_agent`
> 가 이미 구현 — API_Server 는 WebSocket 프레임 핸들러로 연결만.

## 1. 목표

1. `POST /api/v1/agents/register` — RSA 공개키 제출 → Agent JWT 발급
2. `WS /api/v1/agents/ws` — WebSocket 상시 연결 (JWT 인증)
3. WebSocket 프레임: `heartbeat`, `get_credential`
4. `main.py` lifespan 에 `PostgresAgentRepository` 주입

## 2. 범위

**In**
- `app/routers/agents.py` 신규 — 등록 REST + WebSocket
- `app/models/agent.py` 신규 — `AgentRegisterRequest`, `AgentRegisterResponse`
- `app/services/workflow_service.py` 확장 — `register_agent`
- `app/main.py` 확장 — AgentRepository lifespan + 라우터 등록
- `tests/test_agents.py` 신규

**Out**
- Agent → API 방향 실행 결과 push — Execution_Engine 소관
- Agent GPU 라우팅 (ADR-009) — Phase 2
- Agent 해제/삭제 — Phase 2
- 다중 Agent 로드밸런싱 — Phase 2

## 3. 엔드포인트

| 메서드 | 경로 | 인증 | 설명 | 응답 |
|--------|------|------|------|------|
| `POST` | `/api/v1/agents/register` | User JWT | Agent 등록 | 201 `AgentRegisterResponse` |
| `WS` | `/api/v1/agents/ws` | Agent JWT (query param) | 상시 연결 | WebSocket frames |

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
    agent_token: str      # Agent 전용 JWT (subject=agent_id)
```

## 4. Agent JWT

User JWT 와 별도 — `sub` 에 `agent:{agent_id}` 형식 저장.
WebSocket 연결 시 `?token=<agent_jwt>` 쿼리로 인증.
만료: 24시간 (Settings 에서 override 가능).

## 5. WebSocket 프레임 프로토콜

JSON 프레임, `{"type": "<action>", ...}` 형식:

**Client → Server:**
- `{"type": "heartbeat"}` → heartbeat 갱신, 응답: `{"type": "heartbeat_ack"}`
- `{"type": "get_credential", "credential_id": "<uuid>"}` → 재암호화된 자격증명 반환

**Server → Client:**
- `{"type": "heartbeat_ack"}`
- `{"type": "credential", "payload": {"wrapped_key": "...", "nonce": "...", "ciphertext": "..."}}` (base64)
- `{"type": "error", "message": "..."}`

## 6. 함수 증식 방지 가드레일

- `WorkflowService` 에 `register_agent` 1개만 추가
- WebSocket 핸들러는 `agents.py` 라우터 안에서 인라인 처리 — 별도 `AgentManager` 클래스 금지
- credential 재암호화는 `CredentialStore.retrieve_for_agent()` 직접 호출 — 래퍼 금지
- 프레임 디스패치는 `if/elif` 2줄 — `_handle_heartbeat`, `_handle_credential` 헬퍼 금지

## 7. 테스트

1. `test_register_agent_happy` — 201 + agent_id + agent_token
2. `test_register_agent_invalid_key_422`
3. `test_register_agent_not_authenticated_401`
4. `test_ws_heartbeat` — WebSocket 연결 + heartbeat → ack
5. `test_ws_invalid_token_rejected`
6. `test_ws_get_credential` — 재암호화 응답 확인

## 8. 수용 기준

- [ ] 신규 6 테스트 통과
- [ ] 기존 58 테스트 회귀 없음 (총 64+)
- [ ] Agent JWT 가 User JWT 와 구분됨 (`sub` prefix)
- [ ] WebSocket heartbeat 가 DB `last_heartbeat` 갱신
- [ ] `retrieve_for_agent` 호출 시 RSA 재암호화 응답 반환
