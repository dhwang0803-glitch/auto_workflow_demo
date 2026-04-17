# PLAN_10 — Agent daemon credential 복호화 + 주입

> 선행: API_Server PLAN_08 (PR #52, 머지) — execute WS 메시지에 `credential_payloads` 동봉
> 근거 ADR: ADR-013 (하이브리드 전송), ADR-016 (파이프라인 deferral)
> 이후: docs 브랜치 PR — Agent WS 프로토콜 구체 필드 문서화 (이 PR 과 묶어 한 번에)

## 목적

Agent 데몬이 서버로부터 받은 `credential_payloads` 를 **VPC 내부의 RSA 개인키**로
복호화하여 워크플로우 실행 직전에 `resolve_credential_refs` 로 config 에 평문 주입.
이로써 Heavy 세그먼트 Agent 경로의 credential 파이프라인 end-to-end 완결.

## 파일 변경

### 신규
| 파일 | 역할 |
|------|------|
| `src/agent/credential_client.py` | `decrypt_payloads(payloads, private_key)` + `PreDecryptedCredentialStore` (CredentialStore 래퍼) |
| `tests/test_agent_credentials.py` | PreDecryptedStore + decrypt_payloads 단위 + handle_execute E2E |

### 수정
| 파일 | 변경 |
|------|------|
| `src/agent/command_handler.py` | `handle_execute` 가 `agent_private_key_pem` 키워드 인자 받음. `credential_payloads` 있으면 복호화 → `resolve_credential_refs` 로 graph 해소. 실패시 generic `failed`. |
| `src/agent/main.py` | `run_agent` 에 `agent_private_key_pem` 주입 + execute 메시지 라우팅에 전달 |
| `scripts/agent_run.py` | `--agent-private-key <PEM path>` CLI 인자. 파일 없으면 None — credential_ref 있는 그래프에선 실패. |

### 범위 밖
- Agent 개인키 생성/프로비저닝 자동화 (현재는 수동 키페어 생성 + 공개키 등록)
- credential_payloads 단위 개별 실패 처리 (모두 성공 or 전체 실패)
- docs/context 갱신 (별도 docs PR — API_Server PLAN_08 과 이 PR 의 필드명/플로우를 한 번에)

## 구현 상세

### 1. `credential_client.py` — 복호화 + PreDecryptedStore

```python
def decrypt_payloads(payloads, private_key_pem) -> dict[UUID, dict]:
    """credential_id 로 키잉된 평문 dict 맵."""
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
    """Agent 측 CredentialStore 구현. owner_id 필터 무시
    (서버가 이미 검증함). resolve_credential_refs 호환용."""
    ...
```

- `PreDecryptedCredentialStore.bulk_retrieve` 는 owner_id 를 받지만 무시한다 —
  docstring 에 명시. 서버가 이미 credential 발급 시점에 ownership 필터링을 수행했기
  때문에 Agent 가 받은 payload 는 정의상 해당 user 소유.
- 다른 ABC 메서드 (`store`, `retrieve`, `delete`, `retrieve_for_agent`) 는 `NotImplementedError`.

### 2. `command_handler.handle_execute` 확장

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
            # owner_id 는 PreDecryptedStore 가 무시 → dummy 전달
            graph = await resolve_credential_refs(graph, store, owner_id=uuid4())
        except Exception:
            await ws_repo.update_status(
                execution.id, "failed",
                error={"message": "credential resolution failed"},
            )
            return

    await run_workflow(graph, execution, ws_repo, node_registry)
```

- 에러 메시지는 PLAN_08 Worker 와 동일한 `"credential resolution failed"` (generic) — credential_id 미노출.

### 3. `main.py` + `agent_run.py` 배선

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

### 4. 보안 불변식

- 개인키 파일 경로는 고객 VPC 내부 파일 시스템 전제 — Agent 외부에 노출되지 않음.
- `hybrid_decrypt` 실패 (wrong key, tampered ciphertext) → `cryptography.exceptions.InvalidKey` / `InvalidTag` 등 전파 → try/except 로 잡아서 generic `failed`.
- 복호화된 평문은 `decrypt_payloads` 반환 후 `PreDecryptedCredentialStore` 필드에 보관되나 `handle_execute` 스코프 종료 시 GC.
- `resolve_credential_refs` 의 deep copy 특성으로 원본 graph 는 평문 없음 유지 (PLAN_08 Worker 와 동일 성질).

## 테스트 전략

### `test_agent_credentials.py`

**단위 — PreDecryptedStore + decrypt_payloads (DB 불필요):**
1. `test_decrypt_payloads_roundtrip` — 테스트 키페어 생성 → hybrid_encrypt → b64 → decrypt_payloads → 원본 복원
2. `test_pre_decrypted_store_bulk_retrieve` — 2개 credential 저장 → bulk 조회 시 dict 반환
3. `test_pre_decrypted_store_missing_raises` — 없는 id → KeyError
4. `test_pre_decrypted_store_ignores_owner_id` — 임의 owner_id 와도 동작 (서버 필터 전제)

**E2E — handle_execute + 가짜 WS (DB 불필요):**
5. `test_handle_execute_decrypts_and_runs` — credential_payloads 동봉 → 노드가 평문 config 수신 → success
6. `test_handle_execute_no_refs_ignores_payloads` — graph 에 ref 없으면 payloads 가 있어도 그냥 실행 (회귀)
7. `test_handle_execute_refs_without_private_key_fails` — refs 있지만 private_key None → failed + generic message
8. `test_handle_execute_refs_without_payloads_fails` — refs 있지만 credential_payloads 누락 → failed

## 체크리스트

- [ ] `src/agent/credential_client.py`
- [ ] `src/agent/command_handler.py` — 확장 + credential 경로
- [ ] `src/agent/main.py` — run_agent 에 private_key 주입
- [ ] `scripts/agent_run.py` — `--agent-private-key` CLI
- [ ] 테스트 8 pass, 전체 54→62
- [ ] 기존 test_agent.py 회귀 없음 (handle_execute kwarg default None)
- [ ] 커밋 → push → PR

## Out of scope

- Agent 키 프로비저닝 / 회전 자동화 (Phase 2)
- credential_payloads 부분 실패 (전부-성공 or 전체-실패)
- docs/context 갱신 — docs PR 로 분리 (이 PR 머지 후)
