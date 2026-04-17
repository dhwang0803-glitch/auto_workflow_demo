# PLAN_14 — SaaS 확장 노드 4종 (CRM/PM read-side + GitHub + HubSpot)

> 선행: ADR-017 (노드 카탈로그 최소 사양) — CRM/PM 5 최소 + Dev Tools 2 최소
> 기준 충족. PR C (ADR-017 의 마지막 PR).

## 목적

ADR-017 §2 근거:
- CRM/PM **read+write 둘 다** 필요 (80% 의 워크플로우가 read 후 변환 후 write)
- **GitHub** — 개발자 고객 시연의 압도적 다수 사례
- **HubSpot** — 영업/마케팅 체험 고객 시나리오

## 스코프

| node_type | 엔드포인트 | 역할 |
|---|---|---|
| `notion_query_database` | `POST https://api.notion.com/v1/databases/{id}/query` | Notion 페이지 목록 조회 (filter/sort) |
| `airtable_list_records` | `GET https://api.airtable.com/v0/{base}/{table}` | Airtable 레코드 목록 (filterByFormula/maxRecords) |
| `github_create_issue` | `POST https://api.github.com/repos/{owner}/{repo}/issues` | GitHub 이슈 생성 |
| `hubspot_create_contact` | `POST https://api.hubapi.com/crm/v3/objects/contacts` | HubSpot Contact 생성 |

전 노드 `http_bearer` credential_type 재사용 — `api_token` 평문 주입 전제.

## 노드 스펙

### 1. NotionQueryDatabaseNode

```
config:
  api_token: str
  database_id: str
  filter?: dict            # Notion filter object
  sorts?: list             # Notion sorts array
  page_size?: int (default 100)
  timeout_seconds?: int (default 30)

headers: Bearer + Notion-Version: 2022-06-28

response:
  results: list[dict]      # 페이지 원본 그대로
  has_more: bool
  next_cursor: str | None
  count: int
```

### 2. AirtableListRecordsNode

```
config:
  api_token: str
  base_id: str
  table: str
  filter_by_formula?: str  # Airtable formula 문법
  max_records?: int (default 100)
  view?: str               # view name
  timeout_seconds?: int (default 30)

query params: filterByFormula, maxRecords, view

response:
  records: list[dict]      # {id, createdTime, fields}
  offset: str | None       # 다음 페이지 커서
  count: int
```

### 3. GitHubCreateIssueNode

```
config:
  api_token: str           # classic PAT or fine-grained
  owner: str               # repo owner
  repo: str
  title: str
  body?: str
  labels?: list[str]
  assignees?: list[str]
  timeout_seconds?: int (default 30)

headers:
  Authorization: Bearer <api_token>
  Accept: application/vnd.github+json
  X-GitHub-Api-Version: 2022-11-28

response:
  issue_id: int            # issue.id (internal)
  number: int              # issue.number (#42)
  url: str                 # html_url
  state: str               # open/closed
```

### 4. HubSpotCreateContactNode

```
config:
  api_token: str           # private app token
  properties: dict         # {email, firstname, lastname, ...}
  timeout_seconds?: int (default 30)

headers: Authorization: Bearer <api_token>

response:
  contact_id: str          # string in HubSpot
  created_at: str
  properties: dict         # 서버에서 돌아온 (hubspot_owner 등 계산값 포함)
```

## 파일 변경

### 신규
| 파일 | 역할 |
|------|------|
| `src/nodes/notion_query_database.py` | NotionQueryDatabaseNode |
| `src/nodes/airtable_list_records.py` | AirtableListRecordsNode |
| `src/nodes/github_create_issue.py` | GitHubCreateIssueNode |
| `src/nodes/hubspot_create_contact.py` | HubSpotCreateContactNode |
| `tests/test_notion_query_database_node.py` | 단위 테스트 |
| `tests/test_airtable_list_records_node.py` | 단위 테스트 |
| `tests/test_github_create_issue_node.py` | 단위 테스트 |
| `tests/test_hubspot_create_contact_node.py` | 단위 테스트 |

수정: 없음.

## 테스트 전략 (각 노드 3개, 총 12개)

httpx_mock 기반. 각 노드당:
1. `*_success` — 정상 응답 → 주요 필드 추출 검증
2. `*_auth_or_url` — 헤더/URL 합성 검증 (Notion-Version, Airtable 경로, GitHub owner/repo, HubSpot endpoint)
3. `*_error_raises` — 4xx → HTTPStatusError

## 보안 불변식

- 전 노드 `api_token` 은 config 경유 평문만 수용 — 로그 금지
- Airtable/HubSpot/GitHub 노드 모두 `Bearer <token>` 포맷 동일 (Linear 의 bare token 과 구분)
- Notion 은 Bearer + Notion-Version 헤더 필수

## 체크리스트

- [ ] 4 노드 파일 + 4 테스트 파일
- [ ] 전체 테스트 pass (기존 + 12)
- [ ] feature/plan-14-saas-expand push
- [ ] PR → main

## Out of scope

- Notion/Airtable/GitHub/HubSpot 의 update/delete 오퍼레이션 — 수요 확인 후 개별 노드
- GitHub Pulls/PRs/Comments — 이슈 생성만
- HubSpot Deals/Companies/Engagements — Contact 생성만
- Pagination iteration — 노드는 한 번의 API 호출. `loop_items` 와 조합해 고객이 구현
- Rate limit / retry 로직 — raise_for_status 로 전파, 상위 retry 정책 별도
- Salesforce / Pipedrive — 후속 수요 기반
