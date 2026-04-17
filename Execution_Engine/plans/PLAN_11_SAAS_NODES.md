# PLAN_11 — SaaS 통합 노드 4종 (Light 세그먼트 타겟)

> 선행: PLAN_08 (credential resolution) — `http_bearer` credential_type 이 이미
> 정의되어 있고 Worker/Agent 양측에서 평문 주입 경로가 완성됨.

## 목적

현 7개 노드(`http_request`, `condition`, `code`, `slack_notify`, `delay`,
`email_send`, `db_query`)로는 워크플로우 자동화 서비스 상품성이 부족하다
(Zapier 6000+, n8n 400+ 대비). Light 세그먼트가 실제 워크플로우를 조립할 수
있도록 **`http_bearer` credential_type 을 재사용하는 SaaS 통합 노드 4종**을
한 번에 추가한다.

## 스코프

4개 노드, 각 단일 작업:

| node_type | 작업 | 엔드포인트 |
|---|---|---|
| `openai_chat` | Chat Completions (단일 응답) | `POST https://api.openai.com/v1/chat/completions` |
| `notion_create_page` | 페이지 생성 | `POST https://api.notion.com/v1/pages` |
| `airtable_create_record` | 레코드 1건 생성 | `POST https://api.airtable.com/v0/{base_id}/{table}` |
| `linear_create_issue` | 이슈 생성 (GraphQL mutation) | `POST https://api.linear.app/graphql` |

**전 노드 공통:**
- credential 은 `http_bearer` 타입. Worker/Agent 가 `config["api_token"]` 에 평문 주입 (ADR-016 §1 — 노드는 credential_id 를 모름).
- httpx.AsyncClient 로 호출, `raise_for_status()` 로 실패 전파.
- 응답 필드는 **최소 요약만** 반환. 전체 응답 body 는 하위 노드에서 필요하면 `http_request` 로 직접 호출.

## LLM 백엔드 결정 — ADR-008 재확인

ADR-008 의 플랜별 라우팅은 유지:
- Light/Middle → 외부 API (이 PR 의 `openai_chat` 사용)
- Heavy → `Inference_Service` + Gemma 4 26B MoE (추후 브랜치 신설, Agent 개발과 병행)

`openai_chat` 은 Light/Middle 용 MVP. 향후 `LlmNode` 1급 추상화(ADR-007)로
흡수될 수 있으나, 현재는 일반 SaaS 노드와 동일 형태로 시작한다. `provider`
같은 분기 필드는 도입하지 않음 — 필요해지면 그때 추상화.

## 파일 변경

### 신규
| 파일 | 역할 |
|------|------|
| `src/nodes/openai_chat.py` | OpenAIChatNode |
| `src/nodes/notion_create_page.py` | NotionCreatePageNode |
| `src/nodes/airtable_create_record.py` | AirtableCreateRecordNode |
| `src/nodes/linear_create_issue.py` | LinearCreateIssueNode |
| `tests/test_openai_chat_node.py` | httpx_mock 기반 단위 테스트 |
| `tests/test_notion_create_page_node.py` | 동일 |
| `tests/test_airtable_create_record_node.py` | 동일 |
| `tests/test_linear_create_issue_node.py` | 동일 |

수정: 없음. pyproject.toml 도 수정 없음 (httpx 이미 있음).

## 노드 스펙

### 1. OpenAIChatNode

```
config:
  api_token: str      # sk-...
  model: str          # "gpt-4o-mini" 등
  messages: list[{role, content}]
  temperature?: float (default 1.0)
  max_tokens?: int
  timeout_seconds?: int (default 60)

response:
  content: str        # choices[0].message.content
  model: str          # 응답의 model
  finish_reason: str
  usage: {prompt_tokens, completion_tokens, total_tokens}
```

### 2. NotionCreatePageNode

```
config:
  api_token: str
  parent: {database_id: str}  # 또는 {page_id: str}
  properties: dict
  children?: list             # block 배열 (선택)
  timeout_seconds?: int (default 30)

headers:
  Authorization: Bearer <api_token>
  Notion-Version: 2022-06-28
  Content-Type: application/json

response:
  page_id: str
  url: str
```

### 3. AirtableCreateRecordNode

```
config:
  api_token: str      # Personal Access Token
  base_id: str        # appXXXX
  table: str          # table name or tblXXXX
  fields: dict
  timeout_seconds?: int (default 30)

response:
  record_id: str
  created_time: str
  fields: dict        # Airtable 가 반환한 필드 (computed 포함)
```

### 4. LinearCreateIssueNode

Linear API 는 GraphQL. `issueCreate` mutation 사용:

```
config:
  api_token: str
  team_id: str
  title: str
  description?: str
  timeout_seconds?: int (default 30)

headers:
  Authorization: <api_token>   # Linear 는 Bearer prefix 없음
  Content-Type: application/json

body (GraphQL):
  mutation {
    issueCreate(input: {teamId, title, description}) {
      success
      issue { id identifier url }
    }
  }

response:
  issue_id: str
  identifier: str     # "ENG-123"
  url: str
```

## 테스트 전략 (각 노드 3개씩, 총 12개)

모든 테스트는 `httpx_mock` 기반 — 실제 외부 API 호출 없음.

### 공통 패턴 (per node):
1. **success** — 정상 응답 → 기대 필드 추출 검증
2. **auth_header** — `Authorization` 헤더 정확히 포맷됨 (Bearer 포함 여부 노드별)
3. **error_raises** — 4xx/5xx → `httpx.HTTPStatusError`

### Airtable 추가:
- `base_id` + `table` 이 URL 에 정확히 합성됨

### Linear 추가:
- GraphQL body 에 `title`, `teamId` 가 들어가는지

## 보안 불변식

- `api_token` 은 `config` 경유 평문 주입만 수용. 노드 내부 로깅 금지.
- 에러 메시지 정제는 executor 계층 정책 (현 PLAN 범위 외).
- `workflow.graph` 원본 불변 (deep copy 는 resolver 가 보장).

## 체크리스트

- [ ] `src/nodes/openai_chat.py` + 테스트 3개
- [ ] `src/nodes/notion_create_page.py` + 테스트 3개
- [ ] `src/nodes/airtable_create_record.py` + 테스트 4개 (URL 합성 포함)
- [ ] `src/nodes/linear_create_issue.py` + 테스트 4개 (GraphQL body 포함)
- [ ] 전체 테스트 77 pass (기존 65 → 77)
- [ ] 커밋 → push → PR

## Out of scope

- 각 SaaS 의 추가 액션 (Notion query, Airtable list/update, Linear update 등) — 수요 생기면 개별 노드로 확장
- LLM 스트리밍 응답 — MVP 에서 단일 응답만
- OAuth 기반 노드 (Gmail/Sheets 등) — OAuth credential_type 설계 ADR 필요 (별도 로드맵)
- LlmNode 1급 추상화 — ADR-007 에서 Heavy 경로와 함께 다룸. Light 는 일반 노드 형태로 충분.
- `Inference_Service` 로컬 Gemma 4 백엔드 — Heavy 유저 Agent 작업과 함께 신설 예정
