# PLAN — 노드 자격증명 주입 파이프라인 (BYO + Per-execution)

> **성격**: cross-branch 청사진. 실제 구현 PLAN 은 각 브랜치 `plans/` 에 분리.
> **근거 ADR**: [ADR-016](./decisions.md#adr-016--노드-자격증명-주입-파이프라인-별도-plan--후속-adr-로-설계-분리)
> **저장/전송 연관 ADR**: ADR-004 (Fernet 저장), ADR-013 (Agent 전송)
> **상태**: IN PROGRESS — PLAN_09 (PR #47) + PLAN_07 (PR #48) 머지. PLAN_08 (Execution_Engine) 차기.
> **최종 갱신**: 2026-04-17 — §2 책임 분배 재정의 (아래 **Update** 참고)

## 0. 결정 요약

- **공급 모델**: **BYO** — 고객이 자기 SMTP/DB/Slack 자격증명을 우리 시스템에 등록.
  우리는 저장 + 실행 시 주입만 담당. SendGrid/SES 등 SaaS 발신은 본 PLAN 범위 밖.
- **복호화 스코프**: **Per-execution** — workflow 실행 트리거 시점에 필요한 모든
  credential_id 를 **1회 bulk 복호화** → config 에 머지 → dispatch. 노드 호출마다
  재복호화 하지 않음. 메모리 잔존 시간은 "trigger → dispatch" 스코프로 한정.

## 1. Cross-branch 계약 (세 브랜치 모두 준수)

### 1.1. `credential_ref` — workflow graph 내 선언 형식

노드의 `config` 에 `credential_ref` 키를 두고, 실행 파이프라인이 해당 키를
제거 + 복호화 결과를 config 로 머지한다. 노드는 `credential_ref` 를 보지 않음.

```json
{
  "id": "n1",
  "type": "email_send",
  "config": {
    "smtp_host": "smtp.example.com",
    "smtp_port": 587,
    "from": "bot@example.com",
    "to": ["alice@example.com"],
    "subject": "hi",
    "body": "plain",
    "credential_ref": {
      "credential_id": "uuid-...",
      "inject": {
        "user":     "smtp_user",
        "password": "smtp_password"
      }
    }
  }
}
```

- `credential_id`: `credentials.id` UUID
- `inject`: 복호화 결과 dict 의 키 → config 에 들어갈 키 매핑.
  예: decrypted `{"user": "u", "password": "p"}` + inject `{"user":"smtp_user","password":"smtp_password"}`
  → config 에 `{"smtp_user":"u", "smtp_password":"p"}` 추가
- 파이프라인은 **머지 후 원래 `credential_ref` 키 삭제** → 노드 호출시 dict 에 부재

### 1.2. `credential_type` 카탈로그 (MVP)

| type | 필수 dict 키 | 사용 노드 |
|------|--------------|-----------|
| `smtp` | `host`, `port`, `user`, `password` | email_send |
| `postgres_dsn` | `dsn` *또는* `host`+`port`+`user`+`password`+`database` | (예정) db_query |
| `slack_webhook` | `url` | slack_notify (선택적 사용 — 현재 webhook_url 직접 입력도 허용) |
| `http_bearer` | `token` | http_request (Authorization 헤더 주입용) |

- `credentials.type` 컬럼은 enum 이 아닌 **text + CHECK 제약** 으로 시작 (유연성).
  값 집합 확장은 Database migration 으로.
- 각 type 의 dict 키 validation 은 **API_Server 의 credential 등록 라우터**에서만 수행
  (Database 는 JSON blob 저장/복호화만 담당).

### 1.3. `CredentialStore.bulk_retrieve` — Database 신규 메서드

```python
async def bulk_retrieve(
    self, credential_ids: list[UUID], *, owner_id: UUID
) -> dict[UUID, dict]:
    """복호화된 평문 dict 를 credential_id 로 매핑해 반환.
    owner_id 와 일치하지 않는 credential 은 결과에서 제외 (cross-tenant 유출 방지).
    credential_id 가 하나라도 없으면 KeyError — partial resolution 금지.
    """
```

- **ownership 필터 필수** — `WHERE owner_id = :owner_id AND id = ANY(:ids)`
- 누락된 id 가 있으면 전체 실패 (partial success 금지 → 워크플로우가 부분 자격증명으로 실행되는 사고 방지)
- 기존 `retrieve(credential_id)` (단건) 은 유지 — API 에서 등록 직후 검증 용도

### 1.4. `credentials.type` 컬럼 추가 — Database migration

```sql
ALTER TABLE credentials
    ADD COLUMN type text NOT NULL DEFAULT 'unknown';
ALTER TABLE credentials
    ADD CONSTRAINT credentials_type_known
    CHECK (type IN ('smtp', 'postgres_dsn', 'slack_webhook', 'http_bearer', 'unknown'));
```

- 기존 로우는 `unknown` 으로 백필 (테스트 픽스처 말고 prod 에는 없을 것).
- 새 로우는 API 에서 반드시 명시적 type 을 받아야 함.

### 1.5. Agent 모드 — ADR-013 재사용 (변경 없음)

- `retrieve_for_agent(credential_id, agent_public_key_pem)` 가 이미 구현됨.
- Agent 모드 dispatcher 는 `bulk_retrieve` 가 아니라 **credential_id 별로 `retrieve_for_agent` 호출**
  하여 하이브리드 암호문을 모아 Agent 에게 WS push. Agent 는 VPC 내에서 복호화 후 config 머지.
- 즉 **"서버가 평문을 보지 않고 Agent 로 패스스루"** 원칙 유지.

### 1.6. 보안 불변식 (모든 PLAN 이 준수)

1. 평문 credential 은 `workflow_service.execute_workflow` 진입 → Celery `send_task` / Agent WS push
   **사이 스코프 안에서만** 존재. 반환/로그/예외에 노출 금지.
2. 워크플로우 graph 에 **평문을 인라인 저장하지 않는다** — 무조건 `credential_ref` 경유.
   (UI 에서 "이메일 한 통만 보내고 싶은데 등록이 귀찮다" 는 요구는 별도 Phase).
3. 실행 감사 로그 (`execution_node_logs` 또는 신규 `credential_audit`) 에는 `credential_id` 만 기록,
   평문/복호화 결과 절대 금지.
4. credential 을 쓴 execution 이 실패해도 credential 은 로그에 남지 않는다 — 에러 메시지 정제 필요.

## 2. 구현 PR 분할 + 순서

> **Update (2026-04-17)** — 당초 §2 는 "API_Server 가 execute_workflow 에서
> credential_ref 해소" 로 기술했으나 구현 중 현 구조에서는 불가함이 드러났다:
> Celery `send_task` 의 args 에는 `execution_id` 만 담기고 Worker 가 DB 에서
> graph 를 재조회하므로, API_Server 가 in-memory 로 평문을 주입해도 Worker 에
> 전달되지 않는다. 또한 평문을 Celery args 에 싣는 대안은 §1.6 불변식 1번
> ("평문은 `execute_workflow` 진입 → dispatch 사이 스코프 안에서만") 을 Redis
> broker 를 통과하는 형태로 위반한다.
>
> **조정된 책임 분배:**
> - **API_Server (PLAN_07, 머지 완료)** — credential CRUD + `execute_workflow` 에서
>   `bulk_retrieve(ids, owner_id)` 로 **validation only** (존재+소유 검증 후 평문 즉시 폐기).
>   평문 주입은 수행하지 않음.
> - **Execution_Engine (PLAN_08, 신규 — 당초 ③ 의 "~10 LOC" 범위 정식 승격)** —
>   `WorkerContainer` 에 `CredentialStore` 주입 + `_execute()` 가 노드 호출 직전
>   `bulk_retrieve` 로 해소하여 `config` 에 평문 merge + `credential_ref` 키 제거.
>   serverless 경로에 적용. Agent 경로는 여전히 ADR-013 패스스루 (서버 평문 미노출).
>
> 이 재분배로 평문이 **broker/DB 를 거치지 않음** — §1.6 불변식 1번 보장.

```
┌──────────────────────────────────────────────────────────────┐
│ ① Database/plans/PLAN_09_CREDENTIAL_PIPELINE_DB.md  [DONE]   │
│    PR #47 머지 — migration 20260601 + bulk_retrieve 추가     │
│    ~40 LOC + 1 migration + 13 tests                         │
└──────────────────────────────────────────────────────────────┘
                             │  (bulk_retrieve API 확정)
                             ▼
┌──────────────────────────────────────────────────────────────┐
│ ② API_Server/plans/PLAN_07_CREDENTIAL_PIPELINE.md   [DONE]   │
│    PR #48 머지 — credential CRUD + execute_workflow         │
│    **validation only**. GET/LIST 는 deferred (§5 참고).      │
│    ~400 LOC + 10 tests                                      │
└──────────────────────────────────────────────────────────────┘
                             │  (validation 완결, 평문 주입은 아직)
                             ▼
┌──────────────────────────────────────────────────────────────┐
│ ③ Execution_Engine/plans/PLAN_08_CREDENTIAL_RESOLUTION.md    │
│    [TODO]                                                    │
│    - WorkerContainer 에 CredentialStore 주입                 │
│    - _execute() / executor: 노드 호출 직전 credential_ref    │
│      수집 → bulk_retrieve(ids, owner_id) → config merge      │
│      → credential_ref 키 삭제 → 노드에 평문 config 전달     │
│    - Agent 경로: command_handler 가 서버 payload 의          │
│      AgentCredentialPayload 리스트를 VPC 내에서 복호화 후    │
│      동일 방식으로 config merge (ADR-013)                    │
│    - 테스트: ref 해소 / 평문이 응답·로그 등에 노출 안 됨 /   │
│      누락 ref → 실행 실패                                    │
│    추정: ~80 LOC + 5 tests                                   │
└──────────────────────────────────────────────────────────────┘
```

**PR 의존성**: ② 는 ① 의 `bulk_retrieve` 에 의존. ③ 은 ② 머지 후 착수 —
②가 validation 까지만 책임지므로 ③ 이 머지되어야 end-to-end 실 주입이 동작한다.
③ 머지 전까지 Email / DB Query / Slack 등 자격증명 사용 노드는 **단위 테스트 가능**
하지만 **end-to-end 실행 불가** 상태 (PLAN_06/07/08 노드 PR 과 같은 맥락).

## 3. 각 브랜치 PLAN 이 답해야 할 질문

### Database PLAN_09
- `bulk_retrieve` 가 Fernet 단일 키로 전부 복호화? (→ 예, 현 구조 유지)
- 누락 credential_id → 전체 실패. 에러 타입은? (→ 기존 `retrieve` 와 동일 `KeyError`)
- migration 에서 기존 로우 `unknown` 백필 — prod 비어있을 가능성이 높지만 방어적으로 포함

### API_Server PLAN_07 [RESOLVED — PR #48]
- `/credentials` 엔드포인트: `POST /api/v1/credentials` + `DELETE /{id}`. GET/LIST 는
  `CredentialStore.list_by_owner()` 미존재로 **deferred** (후속 Database supplement).
- credential_type 별 dict-key validation 은 Pydantic `Literal` 로 enum 수준만 검증.
  key 강제는 Phase 2 (Frontend UX 확정 후).
- plaintext 는 요청 body 에만, 응답은 `{id, name, type}` 만.
- `credential_ref` 수집 범위는 **depth 1** — 모든 노드의 `config.credential_ref` 만 본다.
  중첩 선언은 현재 허용 안 함.
- Agent 모드 dispatch payload 는 본 PR 스코프 밖. Execution_Engine PLAN_08 에서 ADR-013
  경로로 처리 (서버가 `retrieve_for_agent` 호출 결과를 WS 메시지에 묶어 Agent 에게 넘김).

### Execution_Engine PLAN_08 [TODO]
- credential_ref 해소 시점: 노드 호출 직전 (즉 `executor._run_node` 같은 지점) vs 실행
  시작 시 일괄 (DAG 전체). **blueprint Q2 는 per-execution** 이므로 일괄이 원칙이나,
  구현 편의로 per-node 도 허용 (메모리 수명 동일).
- 해소 실패 시 노드 상태: `failed` 로 기록 + 평문 로그 금지.
- Agent 경로 세부: 서버가 노드별 credential_ref 수집 → `retrieve_for_agent` 루프 →
  WS 메시지에 `credential_payloads: list[{credential_id, AgentCredentialPayload}]` 포함
  → Agent 가 VPC 내 `hybrid_decrypt` 후 동일 merge 로직 실행.

## 4. 테스트 불변식 (모든 PR 이 커버)

- **누설 금지 테스트** — execution 실패 응답, audit 로그, 에러 메시지에 평문 자격증명 문자열이 포함되지 않는다
- **ownership 테스트** — 타 사용자의 credential_id 를 graph 에 적어도 해소 실패
- **credential_ref 제거 테스트** — 노드 `execute` 가 받는 config 에 `credential_ref` 키가 존재하지 않음
- **Agent 모드 패스스루 테스트** — 서버에서 평문을 로그/DB 에 쓰지 않고 암호문 그대로 Agent 에 전달

## 5. 이 청사진 밖 — 명시적 out-of-scope

- **credential rotation/만료** — Phase 2. 현재는 UPDATE 없이 DELETE + 재등록.
- **SendGrid/SES 등 SaaS 발신** — ADR-016 의 모델 B. 별도 PLAN 필요.
- **Frontend credential 등록 UI** — Frontend 브랜치 착수 시 본 PLAN 의 `/credentials` API 를 그대로 소비.
- **credential 공유/팀 권한** — Phase 2. 현재 ownership 은 단일 user.
- **LLM 노드 (ADR-007) 의 API 키 주입** — 동일 파이프라인 재사용. LLM PLAN 이 `credential_type=llm_api_key` 추가하면 자동 호환.

## 6. 파생 문서 위치

- Database 구현 세부: `Database/plans/PLAN_09_CREDENTIAL_PIPELINE_DB.md` (PR #47 머지)
- API_Server 구현 세부: `API_Server/plans/PLAN_07_CREDENTIAL_PIPELINE.md` (PR #48 머지)
- Execution_Engine 구현 세부: `Execution_Engine/plans/PLAN_08_CREDENTIAL_RESOLUTION.md` (신설 예정)
- 본 청사진이 변경되면 ADR-016 Update 섹션으로 역-반영 검토
