# PLAN_08 — Credential Resolution (Execution_Engine 부분)

> 청사진: [`docs/context/PLAN_credential_pipeline.md`](../../docs/context/PLAN_credential_pipeline.md) §2 Update
> 선행: Database PLAN_09 (PR #47) — `bulk_retrieve`. API_Server PLAN_07 (PR #48) — validation.
> 후속: API_Server Agent 경로 credential_payloads 지원 (cross-branch follow-up, 별도 PR)

## 목적

Serverless Worker 가 노드 호출 직전 credential_ref 를 해소하여 `config` 에
평문을 merge 하고, `credential_ref` 키를 제거한 상태로 노드에 전달한다.
평문은 **Worker 프로세스 메모리** 에만 존재 (broker/DB 미경유).

## 파일 변경

### 신규
| 파일 | 역할 |
|------|------|
| `src/runtime/credentials.py` | `resolve_credential_refs(graph, store, owner_id)` — 그래프를 복제하여 평문 주입 |
| `tests/test_credential_resolution.py` | 해소 로직 단위 테스트 |
| `tests/test_dispatcher_credentials.py` | `_execute()` 가 해소 후 run_workflow 호출하는지 E2E |

### 수정
| 파일 | 변경 |
|------|------|
| `src/container.py` | `WorkerContainer` 가 `credential_store` 필드 보유 (프로덕션: Fernet, 테스트: 주입/None) |
| `src/dispatcher/serverless.py` | `_execute()` 가 `credential_store` 받아 run_workflow 전에 해소 수행 |

### 범위 밖 (명시적)
- **Agent 경로** — ADR-013 하이브리드 전송 재사용. API_Server 가 `retrieve_for_agent` 로
  암호문 묶음을 WS 메시지에 포함 → Agent `command_handler` 가 VPC 내 `hybrid_decrypt` 후
  동일 merge. **API_Server 측 WS payload 변경이 동반** 되므로 이 PR 이 아닌 cross-branch
  follow-up.
- 현재 PR 에서 Agent 경로로 credential_ref 가 담긴 그래프가 들어오면 노드 실행시 그냥
  누락된 config 로 실패 — 기존 동작과 동일 (credential 미지원 상태 유지).

## 구현 상세

### 1. `resolve_credential_refs(graph, store, owner_id)` — 순수 해소 함수

```python
async def resolve_credential_refs(
    graph: dict,
    store: CredentialStore,
    owner_id: UUID,
) -> dict:
    # Walk nodes, collect credential_ref.credential_id
    ids: list[UUID] = []
    for node in graph.get("nodes", []):
        ref = (node.get("config") or {}).get("credential_ref")
        if ref and "credential_id" in ref:
            ids.append(UUID(ref["credential_id"]))
    if not ids:
        return graph  # no work, return input (executor treats as immutable anyway)

    decrypted = await store.bulk_retrieve(ids, owner_id=owner_id)

    # Deep copy + in-place mutation of the copy. Keeps the input graph
    # pristine so retries / logs don't accidentally show resolved plaintext.
    import copy
    resolved = copy.deepcopy(graph)
    for node in resolved.get("nodes", []):
        cfg = node.get("config") or {}
        ref = cfg.get("credential_ref")
        if not ref:
            continue
        cid = UUID(ref["credential_id"])
        plaintext = decrypted[cid]
        inject = ref.get("inject", {})
        for src_key, dst_key in inject.items():
            cfg[dst_key] = plaintext[src_key]
        cfg.pop("credential_ref", None)
    return resolved
```

**설계 선택:**
- **per-execution 해소**: 한 번 `bulk_retrieve` 후 전 그래프에 merge. 청사진 Q2 와 일치.
- **deep copy**: 원본 `workflow.graph` 불변성 유지 (retry/로그에 평문 안 남음).
- **`bulk_retrieve` KeyError 전파**: API_Server 가 이미 validation 했으므로 정상 경로에선
  발생 안 함. 방어적으로 dispatch 에서 잡아 execution failed 처리.
- **inject dict 없거나 키 없음 → KeyError 전파**: workflow graph 설계 오류이므로 fail-fast.

### 2. WorkerContainer 확장

```python
class WorkerContainer:
    def __init__(
        self,
        *,
        exec_repo: ExecutionRepository | None = None,
        wf_repo: WorkflowRepository | None = None,
        node_registry: NodeRegistry | None = None,
        credential_store: CredentialStore | None = None,
    ) -> None:
        if exec_repo is not None and wf_repo is not None:
            # Test mode
            self.exec_repo = exec_repo
            self.wf_repo = wf_repo
            self.node_registry = node_registry or default_registry
            self.credential_store = credential_store
            self._engine = None
            return

        # Production mode
        engine = build_engine(os.environ["DATABASE_URL"])
        sm = build_sessionmaker(engine)
        self._engine = engine
        self.exec_repo = PostgresExecutionRepository(sm)
        self.wf_repo = PostgresWorkflowRepository(sm)
        self.node_registry = node_registry or default_registry
        master_key = os.environ.get("CREDENTIAL_MASTER_KEY", "").encode("utf-8")
        self.credential_store = (
            FernetCredentialStore(sm, master_key=master_key) if master_key else None
        )
```

`CREDENTIAL_MASTER_KEY` 환경변수가 **없으면** `credential_store = None` — 개발
환경에서 credential 없이 Worker 실행 허용. 그래프에 credential_ref 가 있으면 dispatch
단계에서 명시적으로 실패.

### 3. `_execute()` 해소 통합

```python
async def _execute(
    execution_id: str,
    *,
    exec_repo: ExecutionRepository,
    wf_repo: WorkflowRepository,
    node_registry: NodeRegistry,
    credential_store: CredentialStore | None = None,
) -> None:
    eid = UUID(execution_id)
    execution = await exec_repo.get(eid)
    if execution is None: ...
    workflow = await wf_repo.get(execution.workflow_id)
    if workflow is None: ...

    try:
        if credential_store is not None:
            graph = await resolve_credential_refs(
                workflow.graph, credential_store, workflow.owner_id
            )
        else:
            graph = workflow.graph
            # Defensive: graph has refs but no store available
            if _graph_has_credential_refs(graph):
                await exec_repo.update_status(
                    eid, "failed",
                    error={"message": "credential store not configured"},
                )
                return
    except KeyError:
        # bulk_retrieve failure (shouldn't happen post API_Server validation,
        # but race condition with credential DELETE between validation and
        # Worker pickup is possible). Generic message — no id leakage.
        await exec_repo.update_status(
            eid, "failed", error={"message": "credential resolution failed"},
        )
        return

    await run_workflow(graph, execution, exec_repo, node_registry)
```

## 테스트 전략

### test_credential_resolution.py (순수 함수, DB 불필요)
1. `test_no_refs_returns_original` — credential_ref 없는 그래프 → 그대로 반환
2. `test_single_ref_injects_and_strips` — 한 노드의 credential_ref 해소 확인 (주입 성공 + credential_ref 키 제거)
3. `test_multiple_refs_bulk_resolve` — 여러 노드의 credential_id 가 한 번의 bulk_retrieve 로 처리됨
4. `test_owner_filter_propagates` — 다른 user 의 credential_id 사용시 KeyError 전파
5. `test_inject_missing_key_raises` — inject 가 존재하지 않는 key 참조 → KeyError
6. `test_original_graph_not_mutated` — 원본 graph 가 변경되지 않음 (deep copy 확인)

### test_dispatcher_credentials.py (E2E with fakes)
1. `test_dispatch_resolves_and_runs` — credential_ref 가 있는 그래프 → 해소 후 노드가 평문 config 로 실행
2. `test_dispatch_without_store_fails_when_refs_present` — credential_store=None + 그래프에 ref → failed
3. `test_dispatch_without_store_works_without_refs` — credential_store=None + ref 없는 그래프 → success (회귀)
4. `test_dispatch_resolve_failure_marks_failed` — bulk_retrieve KeyError → execution failed (generic message)

## 체크리스트

- [ ] `src/runtime/credentials.py` — `resolve_credential_refs` 함수
- [ ] `src/container.py` — WorkerContainer 에 credential_store 추가
- [ ] `src/dispatcher/serverless.py` — `_execute()` 가 해소 수행
- [ ] `tests/fakes.py` — `InMemoryCredentialStore` 가 필요하면 추가 (Database fake 재사용 가능한지 확인)
- [ ] 테스트 10 pass, 전체 33→43 유지
- [ ] 기존 테스트 호환 (`_execute()` 의 `credential_store` kwarg 기본값 None)
- [ ] 커밋 → push → PR

## Out of scope

- Agent 경로 credential 지원 — cross-branch follow-up (API_Server 가 WS payload 구성 + Agent command_handler 가 복호화)
- credential rotation (Phase 2)
- audit logging (후속 결정)
