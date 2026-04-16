# PLAN_06 — Execution 목록 조회 지원 (Database)

> **브랜치**: `Database` · **작성일**: 2026-04-16 · **상태**: Draft
>
> API_Server PLAN_03 (`GET /workflows/{id}/executions`) 이 소비할
> `ExecutionRepository.list_by_workflow` keyset 페이지네이션 메서드와
> 그 선결 스키마 변경을 Database 계층에서 제공한다. 미니 확장.

## 1. 목표

1. `executions` 테이블에 **`created_at`** 불변 타임스탬프 컬럼 추가
2. Keyset 인덱스 `(workflow_id, created_at DESC, id DESC)` 추가
3. `ExecutionRepository` ABC 에 `list_by_workflow` 메서드 추가
4. Postgres 구현 + InMemory fake 동일 계약 구현
5. 테스트

## 2. 범위

**In**
- `migrations/20260416_executions_created_at.sql`
- `schemas/001_core.sql` 동기화
- `models/core.py` Execution ORM — `created_at` 컬럼 + 인덱스
- `repositories/base.py` Execution DTO — `created_at` 필드 + ABC 메서드
- `repositories/execution_repository.py` — Postgres `list_by_workflow`
- `tests/fakes.py` — InMemory `list_by_workflow`
- `tests/test_postgres_repositories.py` 또는 신규 파일 — keyset 테스트

**Out**
- API_Server 라우터/서비스 — PLAN_03 소관
- Execution 생성 시 `created_at` 자동 할당 로직 — `DEFAULT now()` 로 DB 가 처리
- DB 파티셔닝 — 별도 PLAN (트래픽 데이터 미확보)

## 3. Keyset 페이지네이션 사양

```python
async def list_by_workflow(
    self,
    workflow_id: UUID,
    *,
    limit: int = 50,
    cursor: tuple[datetime, UUID] | None = None,
) -> list[Execution]:
```

- 정렬: `created_at DESC, id DESC` (최신 먼저, 동시 생성 시 id tiebreaker)
- cursor: `(created_at, id)` 쌍 — `WHERE (created_at, id) < (?, ?)`
- 첫 페이지: `cursor=None`
- 다음 페이지 cursor: 마지막 row 의 `(created_at, id)`
- API_Server 가 `{items, next_cursor, has_more}` 래퍼를 씌우는 건 PLAN_03 책임

## 4. 함수 증식 방지

- `list_by_workflow` 본문 안에서 cursor 해제는 2~3줄 인라인. `_parse_cursor` 헬퍼 금지.
- Postgres 구현은 단일 `select` 문 하나.
- `_to_dto` 에 `created_at` 매핑 1줄 추가 외에 기존 코드 변경 최소화.

## 5. 테스트

1. **첫 페이지 조회** — 5개 row, limit=3 → 3개 반환
2. **cursor 이어받기** — 첫 페이지 마지막 row cursor → 나머지 2개 반환
3. **빈 결과** — 존재하지 않는 workflow_id → 빈 리스트
4. **tiebreaker** — 동일 `created_at` 2개 row → id DESC 로 안정 정렬

## 6. 수용 기준

- [ ] Migration 적용 후 기존 테스트 전부 통과 (회귀 없음)
- [ ] 신규 keyset 테스트 4개 통과
- [ ] InMemory fake 가 동일 행동 계약 준수
- [ ] `created_at` 이 `NOT NULL DEFAULT now()` 로 설정됨 (`Execution.create` 호출 시 자동)
