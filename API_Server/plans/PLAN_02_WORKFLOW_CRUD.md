# PLAN_02 — Workflow CRUD (API_Server)

> **브랜치**: `API_Server` · **작성일**: 2026-04-15 · **완료일**: 2026-04-15 · **상태**: Done
>
> PLAN_01 인증 위에 첫 비즈니스 CRUD 를 얹는다. DAG 구조 검증 + 플랜 기반
> 쿼터 집행이 포함된다. 실행 트리거/이력 조회/Webhook 은 PLAN_03 이후.

## 1. 목표

1. `/api/v1/workflows` 5개 CRUD 엔드포인트 (모두 `Depends(get_current_user)`)
2. Kahn 위상정렬 기반 DAG 검증 (순환 참조, unreachable 노드, edge ref 무결성)
3. **플랜 기반 쿼터 집행** — light 100 / middle 200 / heavy 500 (Settings override 가능)
4. 목록 응답 래퍼 포맷 — `items + total + limit + plan_tier + approaching_limit`
5. Soft delete (`is_active=false`) — 실행 이력/감사 추적 유지
6. 소유권 검증 실패 시 **404** (enumeration 방지)

## 2. 범위

**In**
- Pydantic: `NodeSpec`, `EdgeSpec`, `WorkflowGraph`, `WorkflowCreate`, `WorkflowUpdate`, `WorkflowSummary`, `WorkflowResponse`, `WorkflowListResponse`
- `app/services/dag_validator.py` — 순수 함수, Kahn 위상정렬
- `app/services/workflow_service.py` — 쿼터 집행 + DAG 검증 + Repository 오케스트레이션
- `app/routers/workflows.py` — CRUD 라우터
- `app/dependencies.py` 확장 — `get_workflow_repo`, `get_workflow_service`
- `app/main.py` 확장 — `PostgresWorkflowRepository` lifespan 주입
- `app/config.py` 확장 — `workflow_limit_light/middle/heavy` + 헬퍼
- `tests/conftest.py` 확장 — `authed_client` fixture
- `tests/test_workflows.py` — CRUD + 쿼터 + 소유권 E2E
- `tests/test_dag_validator.py` — 순수 함수 단위 테스트

**Out (후속 PLAN)**
- 실행 트리거 (`POST /workflows/{id}/execute`, `/activate`) — PLAN_03
- Executions 조회 / Webhook / Agent — PLAN_03+
- 노드 타입별 config 검증 (Q1 의 C 수준) — `NodeCatalog` 채워진 후 Phase 2
- 버전 스냅샷/rollback — Phase 2
- Keyset 페이지네이션 — Phase 2 (현재는 하드 캡만)

## 3. DAG 검증 규칙

1. 노드 id 유일성 — 중복 id 즉시 거부
2. 모든 edge 의 `source`/`target` 이 nodes 에 존재
3. Kahn 위상정렬 완주 → 순환 참조 없음
4. nodes 비어있으면 거부 (최소 1개 필수)

**검증 실패 응답**: 422 + 상세 메시지 (`"cycle detected: a -> b -> a"`)

## 4. 쿼터 집행 사양

| 플랜 | 상한 | 경고 시점 |
|------|-----|---------|
| light | 100 | 90 이상 |
| middle | 200 | 180 이상 |
| heavy | 500 | 450 이상 |

- Settings 에서 환경변수로 override 가능 (`WORKFLOW_LIMIT_LIGHT=150`)
- 카운트 기준: `WorkflowRepository.list_by_owner(owner_id, active_only=True)` 의 길이
- soft-deleted (`is_active=false`) 워크플로우는 쿼터 불산입 → 유저가 삭제/생성 반복 가능
- 초과 생성 시 **403 Forbidden**:
  `"workflow limit reached: 100 workflows for light tier (plan upgrade available)"`

## 5. 응답 래퍼 — `WorkflowListResponse`

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

- 단일 호출로 리스트 + 쿼터 상태 + 경고 플래그 획득
- 목록은 `WorkflowSummary` (graph/settings 제외) 로 페이로드 경량화
- 단건 조회 `GET /workflows/{id}` 는 `WorkflowResponse` (graph + settings 포함)

## 6. 엔드포인트

| 메서드 | 경로 | 설명 | 응답 |
|--------|------|------|------|
| `POST` | `/api/v1/workflows` | 생성 | 201 `WorkflowResponse` |
| `GET` | `/api/v1/workflows` | 목록 (active only) | 200 `WorkflowListResponse` |
| `GET` | `/api/v1/workflows/{id}` | 단건 | 200 `WorkflowResponse` / 404 |
| `PUT` | `/api/v1/workflows/{id}` | 전체 업데이트 | 200 `WorkflowResponse` / 404 |
| `DELETE` | `/api/v1/workflows/{id}` | 소프트 삭제 | 204 / 404 |

**에러 코드**:
| 상황 | HTTP |
|------|------|
| DAG 검증 실패 | 422 |
| 쿼터 초과 | 403 |
| 소유권 없음 / 존재 않음 | 404 |
| 인증 실패 | 401 (Depends 에서 자동) |
| 필드 누락 | 422 (Pydantic) |

## 7. 테스트

- `test_create_workflow_happy_path`
- `test_create_workflow_with_cycle_rejected_422`
- `test_create_workflow_invalid_edge_reference_422`
- `test_create_workflow_quota_enforced_403` (conftest 에서 limit=3 override)
- `test_list_workflows_returns_quota_metadata`
- `test_list_workflows_approaching_limit_flag`
- `test_list_excludes_soft_deleted`
- `test_get_workflow_owned`
- `test_get_workflow_not_owned_returns_404`
- `test_update_workflow_happy`
- `test_delete_workflow_soft_deletes_and_reduces_count`
- `test_dag_validator_empty_nodes_rejected` (단위)
- `test_dag_validator_simple_chain_ok` (단위)
- `test_dag_validator_diamond_ok` (단위)

## 8. 수용 기준

- [x] 20개 신규 테스트 통과 (DAG validator 8 + workflow E2E 12) *(2026-04-15)*
- [x] Database 28 + API_Server 34 = **62/62** 전체 통과
- [x] 쿼터 초과 시 `list_by_owner` 길이 기반 차단 확인 *(test_create_workflow_quota_enforced_403)*
- [x] 존재하지 않는/타 유저 workflow id 접근 시 404 *(test_get_workflow_not_found_returns_404, test_update_nonexistent_returns_404)*
- [x] 목록 응답에 `total/limit/plan_tier/approaching_limit` 전부 포함 *(test_list_workflows_returns_quota_metadata)*
- [x] soft delete 후 목록에서 사라지고 쿼터 카운터 감소 *(test_delete_workflow_soft_deletes_and_reduces_count)*

## 9. 후속 영향

- **PLAN_03** — `POST /workflows/{id}/execute` 가 본 라우터에 추가됨. 생성
  경로의 소유권 검증 패턴을 재사용
- **docs/ADR-001 Update** — plan_tier 쿼터 값 (100/200/500) 을 ADR-001
  Update 섹션으로 기록. 코드 PR 과 함께 docs PR 작성
