# PLAN_02 — DAG Executor (Execution_Engine)

> **브랜치**: `Execution_Engine` · **작성일**: 2026-04-16 · **상태**: Draft
>
> Kahn 위상정렬 순서로 노드를 실행하는 런타임. 의존성이 없는 노드는
> asyncio.gather 로 병렬 실행. ExecutionRepository 로 상태/결과 기록.

## 범위

**In**
- `src/runtime/__init__.py`
- `src/runtime/executor.py` — `run_workflow(graph, execution, repo, registry)`
- `tests/test_executor.py` — 5 테스트

**Out**
- Celery/Agent dispatcher — PLAN_03/04
- 노드 재시도 / 에러 복구 — Phase 2
- Approval pause/resume — Phase 2

## 핵심 함수

```python
async def run_workflow(
    graph: dict,          # {"nodes": [...], "edges": [...]}
    execution: Execution,
    repo: ExecutionRepository,
    registry: NodeRegistry,
) -> None
```

1. Kahn 위상정렬로 실행 순서 (레벨별 그룹) 산출
2. `repo.update_status(execution.id, "running")` 
3. 레벨별로 `asyncio.gather` — 같은 레벨의 노드는 병렬
4. 각 노드: `registry.get(type)()` 로 인스턴스 생성 → `execute(input_data, config)` → `repo.append_node_result`
5. 전부 성공 → `repo.update_status(execution.id, "success")`
6. 예외 발생 → `repo.update_status(execution.id, "failed", error=...)` 

## 함수 증식 방지

- `run_workflow` 한 함수에서 전부 처리. `_build_levels`, `_execute_node`,
  `_update_status` 같은 private 헬퍼 금지.
- Kahn 정렬은 API_Server dag_validator 와 동일 알고리즘이지만, 여기선
  **레벨별 그룹**이 필요 (validator 는 순환 검증만). 별도 함수로 분리하지
  않고 run_workflow 본문에서 인라인.
