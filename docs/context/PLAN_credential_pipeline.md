# PLAN — 노드 자격증명 주입 파이프라인 (BYO + Per-execution)

> **성격**: cross-branch 청사진. 실제 구현 PLAN 은 각 브랜치 `plans/` 에 분리.
> **근거 ADR**: [ADR-016](./decisions.md#adr-016--노드-자격증명-주입-파이프라인-별도-plan--후속-adr-로-설계-분리)
> **저장/전송 연관 ADR**: ADR-004 (Fernet 저장), ADR-013 (Agent 전송)
> **상태**: DRAFT (2026-04-17)

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

```
┌──────────────────────────────────────────────────────────────┐
│ ① Database/plans/PLAN_09_CREDENTIAL_PIPELINE_DB.md           │
│    - 20260XYZ migration: credentials.type 추가               │
│    - FernetCredentialStore.bulk_retrieve(ids, owner_id)      │
│    - 테스트: bulk 성공 / ownership 필터 / 누락 KeyError      │
│    추정: ~40 LOC + 1 migration + 3 tests                    │
└──────────────────────────────────────────────────────────────┘
                             │  (bulk_retrieve API 확정)
                             ▼
┌──────────────────────────────────────────────────────────────┐
│ ② API_Server/plans/PLAN_07_CREDENTIAL_PIPELINE.md            │
│    - CredentialService + /credentials CRUD 라우터           │
│      (POST/GET/DELETE, type-specific 검증)                  │
│    - workflow_service.execute_workflow 에 credential_ref    │
│      해소 로직 삽입 (serverless 경로)                        │
│    - Agent 모드는 ADR-013 경로 그대로 — retrieve_for_agent   │
│      반복 호출 후 WS payload 조립                            │
│    - 테스트: 등록/조회/삭제 + 실행 시 주입 성공/실패          │
│    추정: ~140 LOC + 8-10 tests                              │
└──────────────────────────────────────────────────────────────┘
                             │  (end-to-end 서버측 완결)
                             ▼
┌──────────────────────────────────────────────────────────────┐
│ ③ Execution_Engine — 신규 PLAN 불필요                        │
│    현 executor/dispatcher 는 이미 평문 config 를 받는 구조. │
│    변경 가능성: Agent command_handler.py 가 서버에서 받은    │
│    암호문을 복호화 + 머지하는 부분이 **이미 있는지** 확인.    │
│    없으면 ~10 LOC 추가 (branch-local PR로 처리, 별도 PLAN X)│
└──────────────────────────────────────────────────────────────┘
```

**PR 의존성**: ②는 ①의 `bulk_retrieve` 에 의존하므로 **①머지 후 ② 착수**.
Execution_Engine 변경은 ②머지 후 확인하여 필요할 때만.

## 3. 각 브랜치 PLAN 이 답해야 할 질문

### Database PLAN_09
- `bulk_retrieve` 가 Fernet 단일 키로 전부 복호화? (→ 예, 현 구조 유지)
- 누락 credential_id → 전체 실패. 에러 타입은? (→ 기존 `retrieve` 와 동일 `KeyError`)
- migration 에서 기존 로우 `unknown` 백필 — prod 비어있을 가능성이 높지만 방어적으로 포함

### API_Server PLAN_07
- `/credentials` 엔드포인트 쉐이프 (요청/응답 Pydantic)
- credential_type 별 validation — `smtp` 는 host/port/user/password 필수, 등
- credential plaintext 는 **등록 시에만 요청 body 에 존재**, 응답에는 `id/name/type/created_at` 만
- `execute_workflow` 에서 `credential_ref` 수집 — graph 노드를 순회하며 재귀적으로 찾아야 하는지, depth 1 인지 결정
- Agent 모드 dispatch payload 에 하이브리드 암호문 묶음 형식 (ADR-013 의 `AgentCredentialPayload` 재사용)

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

- Database 구현 세부: `Database/plans/PLAN_09_CREDENTIAL_PIPELINE_DB.md` (신설 예정)
- API_Server 구현 세부: `API_Server/plans/PLAN_07_CREDENTIAL_PIPELINE.md` (신설 예정)
- 본 청사진이 변경되면 ADR-016 Update 섹션으로 역-반영 검토
