# PLAN_14 — Four SaaS expansion nodes (CRM/PM read-side + GitHub + HubSpot)

> Predecessor: ADR-017 (node-catalog minimum spec) — meets the CRM/PM
> 5-minimum + Dev Tools 2-minimum criteria. PR C (ADR-017's last PR).

## Purpose

Per ADR-017 §2:
- CRM/PM needs **both read and write** (80% of workflows are read → transform → write)
- **GitHub** — overwhelmingly the top example for developer customers
- **HubSpot** — the trial scenario for sales/marketing customers

## Scope

| node_type | Endpoint | Role |
|---|---|---|
| `notion_query_database` | `POST https://api.notion.com/v1/databases/{id}/query` | List Notion pages (filter/sort) |
| `airtable_list_records` | `GET https://api.airtable.com/v0/{base}/{table}` | List Airtable records (filterByFormula/maxRecords) |
| `github_create_issue` | `POST https://api.github.com/repos/{owner}/{repo}/issues` | Create a GitHub issue |
| `hubspot_create_contact` | `POST https://api.hubapi.com/crm/v3/objects/contacts` | Create a HubSpot Contact |

All nodes reuse the `http_bearer` credential_type — `api_token` plaintext-injection premise.

## Node specs

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
  results: list[dict]      # pages as-is
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
  filter_by_formula?: str  # Airtable formula syntax
  max_records?: int (default 100)
  view?: str               # view name
  timeout_seconds?: int (default 30)

query params: filterByFormula, maxRecords, view

response:
  records: list[dict]      # {id, createdTime, fields}
  offset: str | None       # cursor for the next page
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
  properties: dict         # returned by the server (includes computed values like hubspot_owner)
```

## File changes

### New
| File | Role |
|------|------|
| `src/nodes/notion_query_database.py` | NotionQueryDatabaseNode |
| `src/nodes/airtable_list_records.py` | AirtableListRecordsNode |
| `src/nodes/github_create_issue.py` | GitHubCreateIssueNode |
| `src/nodes/hubspot_create_contact.py` | HubSpotCreateContactNode |
| `tests/test_notion_query_database_node.py` | Unit tests |
| `tests/test_airtable_list_records_node.py` | Unit tests |
| `tests/test_github_create_issue_node.py` | Unit tests |
| `tests/test_hubspot_create_contact_node.py` | Unit tests |

Modified: none.

## Test strategy (3 per node, 12 total)

httpx_mock based. Per node:
1. `*_success` — happy response → verify main field extraction
2. `*_auth_or_url` — verify header / URL composition (Notion-Version, Airtable path, GitHub owner/repo, HubSpot endpoint)
3. `*_error_raises` — 4xx → HTTPStatusError

## Security invariants

- All nodes accept `api_token` only as plaintext via config — no logging
- Airtable / HubSpot / GitHub nodes all use the `Bearer <token>` format (distinct from Linear's bare token)
- Notion requires Bearer + Notion-Version header

## Checklist

- [ ] 4 node files + 4 test files
- [ ] All tests pass (existing + 12)
- [ ] Push feature/plan-14-saas-expand
- [ ] PR → main

## Out of scope

- Update/delete operations for Notion/Airtable/GitHub/HubSpot — separate nodes after demand validation
- GitHub Pulls/PRs/Comments — issue creation only
- HubSpot Deals/Companies/Engagements — Contact creation only
- Pagination iteration — node makes one API call; combine with `loop_items` for the customer to implement
- Rate-limit / retry logic — propagated via raise_for_status, upper-layer retry policy is separate
- Salesforce / Pipedrive — based on later demand
