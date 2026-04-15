# PLAN_04 — Approval 알림 발송 이력 (audit trail)

> **브랜치**: `Database` · **작성일**: 2026-04-15 · **완료일**: 2026-04-15 · **상태**: Done
>
> ADR-007 의 `ApprovalNode` 2-track 알림(email + slack) 이 실제로 어디에
> 언제 어떤 결과로 발송됐는지를 영속화한다. PLAN_04 는 "발송 로직" 이 아니라
> **"발송 이력을 어떻게 저장할지"** 만 다룬다. 실제 발송은 `API_Server` 또는
> 별도 워커의 책임이며 이 PLAN 의 Repository 를 통해 기록한다.

## 1. 목표

1. `approval_notifications` 테이블 신규 — 시도당 1행 append-only audit trail
2. `ApprovalNotificationRepository` ABC + Postgres/InMemory 구현
3. 미도달(`queued`/`failed`) 대시보드 쿼리 경로를 부분 인덱스로 확보
4. **인박스(읽기 경로)는 이 PLAN 범위 밖** — 인박스는 `executions WHERE status='paused'` 쿼리일 뿐 독립 테이블이 아님

## 2. 범위

**In**
- DDL: `approval_notifications` (단순 테이블, 파티셔닝 없음)
- Repository ABC + DTO + ORM + Postgres 구현 + InMemory 더블
- 통합 테스트: 시도 append / 미도달 리스트 / execution 단위 조회
- ADR-007 의 채널(`email`, `slack`) 과 일치하는 CHECK 제약

**Out (후속/타 브랜치)**
- **실제 발송 로직** — `API_Server` 또는 별도 워커. 이 PLAN 은 기록 경로만
- **`NotificationChannel` 어댑터** — SMTP/Slack API 호출. `API_Server` 책임
- **발송 재시도 정책** — 어느 브랜치가 소유할지는 별도 논의
- **운영 대시보드 UI** — Frontend 책임. 본 PLAN 은 쿼리 경로만 확보
- **파티셔닝** — 볼륨 분석 결과 불요 (§3 참조)
- **이메일/Slack ID 재사용 / GDPR 삭제 정책** — 운영 PLAN 에서 별도 논의

## 3. 볼륨 분석 (파티셔닝 결정 근거)

| 축 | 가정 | 값 |
|---|---|---|
| 고객 수 (MVP~Phase 1) | | 100 |
| 고객당 워크플로우 | | 30 |
| ApprovalNode 사용 비율 | | 15% |
| 승인 노드당 일 실행 | | 평균 5회 |
| **일간 승인 이벤트** | | ~2K |
| 이벤트당 평균 알림 행 | email + slack + 1회 재시도 | 3 |
| **연간 `approval_notifications` 행** | | ~2.2M |
| 행당 크기 (JSONB 포함) | | ~300 B |
| **연간 테이블 부피** | | ~0.7 GB |

**결론**: 파티셔닝 없이 단순 테이블 + 인덱스로 충분. ADR-011 의 "선제
파티셔닝" 철학은 `execution_node_logs` (노드당 N 행으로 곱해짐) 처럼 볼륨이
실제로 커지는 테이블에 한정한다. 본 테이블은 이벤트당 O(1~5) 규모라 수천만
행 도달 시점(≈10년 이상 누적) 까지 단순 구조로 감당 가능.

## 4. 테이블 설계

### 4.1 `approval_notifications`

| 컬럼 | 타입 | 비고 |
|------|------|------|
| `id` | `uuid PK DEFAULT gen_random_uuid()` | |
| `execution_id` | `uuid NOT NULL REFERENCES executions(id) ON DELETE CASCADE` | |
| `node_id` | `text NOT NULL` | `paused_at_node` 와 동일 값이 들어옴 |
| `recipient` | `text NOT NULL` | 평문 이메일 주소 또는 Slack user id. **조회 병목 회피 목적으로 평문 저장** (GDPR 삭제는 별도 PLAN) |
| `channel` | `text NOT NULL` | CHECK `IN ('email','slack')` — ADR-007 2-track |
| `status` | `text NOT NULL` | CHECK `IN ('queued','sent','failed','bounced')` |
| `attempt` | `integer NOT NULL DEFAULT 1` | 호출자 명시 전달 (재시도 루프 소유) |
| `error` | `jsonb NULL` | 실패 시 provider 응답/에러 메시지 |
| `sent_at` | `timestamptz NULL` | `status='sent'` 일 때만 채워짐 |
| `created_at` | `timestamptz NOT NULL DEFAULT now()` | 행 생성 (시도 시작) 시각 |

### 4.2 인덱스

```sql
-- (a) execution 상세 조회: 특정 실행/노드의 모든 알림 이력
CREATE INDEX idx_approval_notif_execution
    ON approval_notifications (execution_id, node_id, created_at DESC);

-- (b) 미도달 대시보드: queued 나 failed 로 남아 있는 것만 추적
CREATE INDEX idx_approval_notif_undelivered
    ON approval_notifications (created_at)
    WHERE status IN ('queued', 'failed');
```

부분 인덱스 (b) 가 핵심: "미도달 상태 로우만" 인덱스에 포함되어 대시보드
쿼리(`WHERE status IN ('queued','failed') AND created_at < now() - interval`) 가
매우 작은 인덱스 스캔으로 끝남.

### 4.3 발송 실패 ↔ Approval 상태머신 분리

발송 실패가 `executions.status` 에 영향을 주지 **않는다**. 근거:
- (+) 일시적 SMTP/Slack 장애가 워크플로우 실행 상태로 전파되면 알림 인프라
  장애가 곧 자동화 장애로 확대된다.
- (+) 발송 실패는 이 테이블의 `status='failed'` 로 기록되고, 미도달
  대시보드(부분 인덱스 경로) 를 통해 운영팀이 별도로 대응한다.
- (−) 극단적 시나리오(모든 채널 영구 실패) 에서 Approval 대기가 사람 눈에
  띄지 않음 → 운영 대시보드의 "24시간+ 미도달 알림" 알람을 에스컬레이션
  루트로 사용. 이 알람 자체는 본 PLAN 범위 밖.

## 5. Repository

### 5.1 DTO

```python
@dataclass
class ApprovalNotification:
    id: UUID
    execution_id: UUID
    node_id: str
    recipient: str
    channel: Literal["email", "slack"]
    status: Literal["queued", "sent", "failed", "bounced"]
    attempt: int
    error: dict | None = None
    sent_at: datetime | None = None
    created_at: datetime | None = None
```

### 5.2 ABC

```python
class ApprovalNotificationRepository(ABC):
    @abstractmethod
    async def record(self, notification: ApprovalNotification) -> None: ...

    @abstractmethod
    async def list_for_execution(
        self, execution_id: UUID
    ) -> list[ApprovalNotification]: ...

    @abstractmethod
    async def list_undelivered(
        self, *, older_than: timedelta
    ) -> list[ApprovalNotification]: ...
```

- `record` — append-only. 호출자는 매 시도마다 새 id 를 만들어 호출.
- `list_for_execution` — execution 상세 뷰/감사 로그용. `(node_id, created_at DESC)` 순.
- `list_undelivered(older_than=timedelta(hours=24))` — 운영 대시보드. 부분 인덱스 경로.

## 6. 산출물

| 경로 | 내용 |
|------|------|
| `schemas/004_approval_notifications.sql` | 테이블 + CHECK + 인덱스 2개 |
| `migrations/20260515_approval_notifications.sql` | 004 포함 마이그레이션 |
| `src/models/notifications.py` | SQLAlchemy ORM |
| `src/repositories/base.py` | ABC + DTO 추가 |
| `src/repositories/approval_notification_repository.py` | Postgres 구현 |
| `tests/fakes.py` | `InMemoryApprovalNotificationRepository` 추가 |
| `tests/test_approval_notifications.py` | 통합 테스트 (append / list / undelivered 필터) |
| `tests/test_schema_loads.py` | 기대 테이블 집합에 `approval_notifications` 추가 |

## 7. 수용 기준

- [x] 004 마이그레이션이 깨끗이 적용 *(2026-04-15)*
- [x] `record()` append + `list_for_execution()` DESC 순 반환 *(test_append_and_list_for_execution)*
- [x] `list_undelivered(older_than=timedelta(hours=1))` 가 fresh / sent 를 제외하고 오래된 queued/failed 만 반환 *(test_list_undelivered_filters_by_age_and_status)*
- [x] CHECK 제약이 잘못된 `channel`/`status` 값을 거부 *(test_check_constraints_reject_bad_values)*
- [x] `test_schema_loads` 가 004 포함 전체 스키마 복원 후 `approval_notifications` 확인

## 8. 오픈 이슈

1. **`recipient` 의 GDPR 대응** — 평문 이메일 저장은 성능 최우선 결정.
   삭제 요청 처리 경로는 운영 PLAN 에서 정의. 현재 DDL 은 삭제를 위해
   `DELETE FROM ... WHERE recipient = ?` 만으로 충분.
2. **Slack user id vs 이메일 구분** — 같은 `recipient` 컬럼에 두 형식이 섞임.
   쿼리 시 `channel` 로 분기하는 것이 현재 규칙. 구조화가 필요하면 다음 PLAN
   에서 JSONB 로 승격.
3. **`attempt` 카운터 소유** — ADR-011 과 동일 원칙: 호출자(= 발송 워커) 가
   자기 루프에서 관리. DB 는 명시 전달된 값을 저장만.
4. **보존 삭제 정책** — 무제한 누적. 연간 0.7 GB 수준이라 당분간 무시 가능.
   볼륨 10GB 초과 시점에 별도 PLAN.

## 9. 후속 PLAN 영향

- **PLAN_05 (Agent 재암호화)** — 무관.
- **운영/대시보드 PLAN** — 이 테이블의 `list_undelivered` 를 폴링해 알람 생성.
- **실제 발송 로직** — `API_Server` 또는 워커. 본 Repository 를 DI 로 주입받아
  매 발송 시도마다 `record()` 호출.
