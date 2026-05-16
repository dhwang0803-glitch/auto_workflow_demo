# PLAN_02 — Workflow CRUD (API_Server)

> **Branch**: `API_Server` · **Drafted**: 2026-04-15 · **Completed**: 2026-04-15 · **Status**: Done
>
> Layers the first business CRUD onto the PLAN_01 auth foundation. Includes
> DAG-structure validation + plan-based quota enforcement. Execution
> triggering / history queries / Webhooks land in PLAN_03 and later.

## 1. Goals

1. 5 CRUD endpoints under `/api/v1/workflows` (all using `Depends(get_current_user)`)
2. Kahn-topological-sort-based DAG validation (cycles, unreachable nodes, edge-ref integrity)
3. **Plan-based quota enforcement** — light 100 / middle 200 / heavy 500 (overridable via Settings)
4. List-response wrapper shape — `items + total + limit + plan_tier + approaching_limit`
5. Soft delete (`is_active=false`) — preserves execution history / audit trail
6. **404** on ownership-check failure (anti-enumeration)

## 2. Scope

**In**
- Pydantic: `NodeSpec`, `EdgeSpec`, `WorkflowGraph`, `WorkflowCreate`, `WorkflowUpdate`, `WorkflowSummary`, `WorkflowResponse`, `WorkflowListResponse`
- `app/services/dag_validator.py` — pure functions, Kahn topological sort
- `app/services/workflow_service.py` — quota enforcement + DAG validation + Repository orchestration
- `app/routers/workflows.py` — CRUD router
- `app/dependencies.py` extension — `get_workflow_repo`, `get_workflow_service`
- `app/main.py` extension — `PostgresWorkflowRepository` lifespan injection
- `app/config.py` extension — `workflow_limit_light/middle/heavy` + helper
- `tests/conftest.py` extension — `authed_client` fixture
- `tests/test_workflows.py` — CRUD + quota + ownership E2E
- `tests/test_dag_validator.py` — pure-function unit tests

**Out (follow-up PLAN)**
- Execution trigger (`POST /workflows/{id}/execute`, `/activate`) — PLAN_03
- Executions query / Webhook / Agent — PLAN_03+
- Per-node-type config validation (Q1's "level C") — Phase 2, after `NodeCatalog` is populated
- Version snapshot / rollback — Phase 2
- Keyset pagination — Phase 2 (currently only hard cap)

## 3. DAG validation rules

1. Unique node ids — duplicate id is rejected outright
2. Every edge's `source`/`target` must exist in `nodes`
3. Kahn topological sort completes → no cycles
4. Reject empty `nodes` (≥ 1 required)

**Validation failure response**: 422 + detail message (`"cycle detected: a -> b -> a"`)

## 4. Quota enforcement spec

| Plan | Cap | Warning threshold |
|------|-----|-------------------|
| light | 100 | ≥ 90 |
| middle | 200 | ≥ 180 |
| heavy | 500 | ≥ 450 |

- Overridable per Settings env var (`WORKFLOW_LIMIT_LIGHT=150`)
- Count basis: length of `WorkflowRepository.list_by_owner(owner_id, active_only=True)`
- Soft-deleted (`is_active=false`) workflows don't count → users can delete/create repeatedly
- On exceeding the cap → **403 Forbidden**:
  `"workflow limit reached: 100 workflows for light tier (plan upgrade available)"`

## 5. Response wrapper — `WorkflowListResponse`

```json
{
  "items": [
    {"id": "...", "name": "...", "is_active": true,
     "created_at": "...", "updated_at": "..."}
  ],
  "total": 87,
  "limit": 100,
  "plan_tier": "light",
  "approaching_limit": false
}
```

- A single call returns list + quota state + warning flag
- The list uses `WorkflowSummary` (excluding graph/settings) to keep the payload light
- Single-row `GET /workflows/{id}` returns `WorkflowResponse` (graph + settings included)

## 6. Endpoints

| Method | Path | Description | Response |
|--------|------|-------------|----------|
| `POST` | `/api/v1/workflows` | Create | 201 `WorkflowResponse` |
| `GET` | `/api/v1/workflows` | List (active only) | 200 `WorkflowListResponse` |
| `GET` | `/api/v1/workflows/{id}` | Single | 200 `WorkflowResponse` / 404 |
| `PUT` | `/api/v1/workflows/{id}` | Full update | 200 `WorkflowResponse` / 404 |
| `DELETE` | `/api/v1/workflows/{id}` | Soft delete | 204 / 404 |

**Error codes**:
| Condition | HTTP |
|-----------|------|
| DAG validation failure | 422 |
| Quota exceeded | 403 |
| No ownership / does not exist | 404 |
| Auth failure | 401 (auto via Depends) |
| Field missing | 422 (Pydantic) |

## 7. Tests

- `test_create_workflow_happy_path`
- `test_create_workflow_with_cycle_rejected_422`
- `test_create_workflow_invalid_edge_reference_422`
- `test_create_workflow_quota_enforced_403` (conftest overrides limit=3)
- `test_list_workflows_returns_quota_metadata`
- `test_list_workflows_approaching_limit_flag`
- `test_list_excludes_soft_deleted`
- `test_get_workflow_owned`
- `test_get_workflow_not_owned_returns_404`
- `test_update_workflow_happy`
- `test_delete_workflow_soft_deletes_and_reduces_count`
- `test_dag_validator_empty_nodes_rejected` (unit)
- `test_dag_validator_simple_chain_ok` (unit)
- `test_dag_validator_diamond_ok` (unit)

## 8. Acceptance criteria

- [x] 20 new tests pass (DAG validator 8 + workflow E2E 12) *(2026-04-15)*
- [x] Database 28 + API_Server 34 = **62/62** overall pass
- [x] Verified that quota enforcement uses `list_by_owner` length *(test_create_workflow_quota_enforced_403)*
- [x] Accessing a nonexistent / other-owner workflow id returns 404 *(test_get_workflow_not_found_returns_404, test_update_nonexistent_returns_404)*
- [x] The list response includes all of `total/limit/plan_tier/approaching_limit` *(test_list_workflows_returns_quota_metadata)*
- [x] After soft delete the item disappears from the list and the quota counter drops *(test_delete_workflow_soft_deletes_and_reduces_count)*

## 9. Downstream impact

- **PLAN_03** — `POST /workflows/{id}/execute` is added to this router.
  Reuses the ownership-check pattern from the create path
- **docs/ADR-001 Update** — record the plan_tier quotas (100/200/500) in the
  ADR-001 Update section. Submit the docs PR alongside the code PR
