# PLAN_02 — DAG Executor (Execution_Engine)

> **Branch**: `Execution_Engine` · **Written**: 2026-04-16 · **Status**: Draft
>
> Runtime that executes nodes in Kahn topological-sort order. Nodes with
> no remaining dependencies run in parallel via `asyncio.gather`. Status
> and results are recorded via `ExecutionRepository`.

## Scope

**In**
- `src/runtime/__init__.py`
- `src/runtime/executor.py` — `run_workflow(graph, execution, repo, registry)`
- `tests/test_executor.py` — 5 tests

**Out**
- Celery / Agent dispatcher — PLAN_03/04
- Node retry / error recovery — Phase 2
- Approval pause/resume — Phase 2

## Core function

```python
async def run_workflow(
    graph: dict,          # {"nodes": [...], "edges": [...]}
    execution: Execution,
    repo: ExecutionRepository,
    registry: NodeRegistry,
) -> None
```

1. Compute execution order (level-by-level groups) with Kahn's topological sort
2. `repo.update_status(execution.id, "running")`
3. Run each level with `asyncio.gather` — nodes in the same level run in parallel
4. Each node: instantiate via `registry.get(type)()` → `execute(input_data, config)` → `repo.append_node_result`
5. All succeeded → `repo.update_status(execution.id, "success")`
6. On exception → `repo.update_status(execution.id, "failed", error=...)`

## Avoid function sprawl

- `run_workflow` handles everything inline. No private helpers like
  `_build_levels`, `_execute_node`, or `_update_status`.
- The Kahn sort uses the same algorithm as the API_Server dag_validator,
  but here we need **level-grouped** output (the validator only checks for
  cycles). Don't extract a separate function — keep it inline in
  `run_workflow`.
