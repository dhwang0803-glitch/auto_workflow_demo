# PLAN_08 — Agent Execute WS 에 credential_payloads 동봉 (API_Server 측)

> 선행: PLAN_07 (`/credentials` CRUD + serverless validation), Database bulk_retrieve + retrieve_for_agent
> 후속: Execution_Engine follow-up — Agent daemon 측 복호화 + resolve_credential_refs 재사용

## 목적

Heavy 세그먼트 활성화. Serverless 경로는 Worker 가 `CredentialStore` 로 DB 에서 직접
복호화 (PLAN_08 EE). Agent 경로는 고객 VPC 에서 DB 접근 불가 → 서버가 노드별
credential 을 **Agent 공개키로 재암호화** (ADR-013 하이브리드) 해서 execute WS 메시지에
동봉. Agent 가 개인키로 복호화 후 동일한 `resolve_credential_refs` 재사용.

## 파일 변경

### 신규
| 파일 | 역할 |
|------|------|
| `tests/test_agent_credential_payload.py` | WS execute 메시지의 `credential_payloads` 필드 E2E 검증 |

### 수정
| 파일 | 변경 |
|------|------|
| `app/services/workflow_service.py` | 생성자에 `credential_store` 주입, `execute_workflow` agent 분기에서 `retrieve_for_agent` 루프 → base64 → `credential_payloads` 추가. ref_ids 수집을 함수 상단으로 끌어올려 validation 과 agent payload 가 같은 변수 공유. |
| `app/container.py` | `WorkflowService(credential_store=self.credential_store, ...)` 로 배선 |

### 범위 밖
- Agent daemon 측 복호화 / resolve (Execution_Engine follow-up PR)
- credential_payloads 가 있는데 store 미구성된 상태 — container 에서 동일 master_key 로 조립되므로 정상 배포에서는 발생 안 함 (방어 코드만 최소)

## 구현 상세

### `WorkflowService` 생성자 확장

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

### `execute_workflow` 재구성

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

## 보안 불변식

- `retrieve_for_agent` 는 ADR-013 경로 — 서버는 평문을 일회 보지만 즉시 Agent 공개키로 재암호화
- WS 메시지는 이미 복호화된 평문이 아니라 Agent 공개키로 암호화된 envelope 전달 → 네트워크 상에서 안전
- `credential_payloads` 빈 배열이라도 Agent 가 credential_ref 있는 그래프에서 실패하면 안전 (Agent 가 자체 검증 — EE follow-up)

## 테스트 전략

### test_agent_credential_payload.py (E2E, DATABASE_URL 필요)

테스트 시 RSA 키페어를 동적 생성 (cryptography 라이브러리). 기존 test_agents.py 의
하드코딩 RSA_PUB_KEY 대신 fresh keypair 사용.

1. `test_execute_agent_includes_credential_payloads` — credential 등록 + workflow(credential_ref) + agent 연결 → execute → WS 가 받는 execute 메시지에 `credential_payloads` 존재, 길이 1, 각 필드 b64 디코드 가능
2. `test_execute_agent_no_refs_sends_empty_payloads` — ref 없는 workflow → credential_payloads=[]
3. `test_execute_agent_multiple_refs_each_payload_distinct` — 2개 credential → 2개 payloads, credential_id 서로 다름

## 체크리스트

- [ ] `workflow_service.py` — credential_store 주입 + execute_workflow agent 분기에 credential_payloads 추가
- [ ] `container.py` — 배선
- [ ] 3 tests (Docker Postgres 필요)
- [ ] 기존 72 tests 회귀 없음
- [ ] 커밋 → push → PR

## Out of scope

- Agent daemon 측 복호화 (다음 PR, Execution_Engine)
- credential_payloads 가 있는데 store 없을 때 defensive failed — container 가 함께 조립하므로 방어 불필요, 로그 경고만
- Agent reconnect 중 credential 만료 — 후속
