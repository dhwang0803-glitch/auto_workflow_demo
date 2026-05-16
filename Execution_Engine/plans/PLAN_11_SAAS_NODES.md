# PLAN_11 — Four SaaS integration nodes (Light-segment target)

> Predecessor: PLAN_08 (credential resolution) — the `http_bearer`
> credential_type is already defined and the plaintext-injection path is
> complete on both Worker and Agent.

## Purpose

The current 7 nodes (`http_request`, `condition`, `code`, `slack_notify`,
`delay`, `email_send`, `db_query`) are not enough for a workflow
automation product (Zapier has 6000+, n8n 400+). To let the Light
segment actually compose workflows, **we add four SaaS integration nodes
that reuse the `http_bearer` credential_type** in one batch.

## Scope

4 nodes, each a single action:

| node_type | Action | Endpoint |
|---|---|---|
| `openai_chat` | Chat Completions (single response) | `POST https://api.openai.com/v1/chat/completions` |
| `notion_create_page` | Create a page | `POST https://api.notion.com/v1/pages` |
| `airtable_create_record` | Create one record | `POST https://api.airtable.com/v0/{base_id}/{table}` |
| `linear_create_issue` | Create an issue (GraphQL mutation) | `POST https://api.linear.app/graphql` |

**Common to all nodes:**
- Credential is `http_bearer` type. Worker/Agent inject plaintext into `config["api_token"]` (ADR-016 §1 — the node never sees credential_id).
- Call with httpx.AsyncClient, propagate failure with `raise_for_status()`.
- Response fields are **minimal summaries only**. If downstream nodes need the full body, they should call `http_request` directly.

## LLM backend decision — reaffirming ADR-008

ADR-008's plan-based routing stands:
- Light/Middle → external API (this PR's `openai_chat`)
- Heavy → `Inference_Service` + Gemma 4 26B MoE (new branch later, parallel with Agent development)

`openai_chat` is the MVP for Light/Middle. It may later be absorbed into
a first-class `LlmNode` abstraction (ADR-007), but for now we start with
the same shape as the other SaaS nodes. We do not introduce a branching
field like `provider` — abstract only when needed.

## File changes

### New
| File | Role |
|------|------|
| `src/nodes/openai_chat.py` | OpenAIChatNode |
| `src/nodes/notion_create_page.py` | NotionCreatePageNode |
| `src/nodes/airtable_create_record.py` | AirtableCreateRecordNode |
| `src/nodes/linear_create_issue.py` | LinearCreateIssueNode |
| `tests/test_openai_chat_node.py` | httpx_mock-based unit tests |
| `tests/test_notion_create_page_node.py` | same |
| `tests/test_airtable_create_record_node.py` | same |
| `tests/test_linear_create_issue_node.py` | same |

Modified: none. No `pyproject.toml` change (httpx already present).

## Node specs

### 1. OpenAIChatNode

```
config:
  api_token: str      # sk-...
  model: str          # e.g. "gpt-4o-mini"
  messages: list[{role, content}]
  temperature?: float (default 1.0)
  max_tokens?: int
  timeout_seconds?: int (default 60)

response:
  content: str        # choices[0].message.content
  model: str          # response's model
  finish_reason: str
  usage: {prompt_tokens, completion_tokens, total_tokens}
```

### 2. NotionCreatePageNode

```
config:
  api_token: str
  parent: {database_id: str}  # or {page_id: str}
  properties: dict
  children?: list             # block array (optional)
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
  fields: dict        # fields returned by Airtable (includes computed)
```

### 4. LinearCreateIssueNode

The Linear API is GraphQL. Use the `issueCreate` mutation:

```
config:
  api_token: str
  team_id: str
  title: str
  description?: str
  timeout_seconds?: int (default 30)

headers:
  Authorization: <api_token>   # Linear has no Bearer prefix
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

## Test strategy (3 per node, 12 total)

All tests use `httpx_mock` — no real external API calls.

### Common pattern (per node):
1. **success** — happy response → verify expected field extraction
2. **auth_header** — `Authorization` header formatted correctly (Bearer prefix is per-node)
3. **error_raises** — 4xx/5xx → `httpx.HTTPStatusError`

### Airtable extra:
- `base_id` + `table` are composed into the URL correctly

### Linear extra:
- GraphQL body contains `title`, `teamId`

## Security invariants

- `api_token` is only accepted via plaintext injection through `config`. No node-internal logging.
- Sanitizing error messages is an executor-layer policy (out of scope here).
- `workflow.graph` original is immutable (deep copy guaranteed by the resolver).

## Checklist

- [ ] `src/nodes/openai_chat.py` + 3 tests
- [ ] `src/nodes/notion_create_page.py` + 3 tests
- [ ] `src/nodes/airtable_create_record.py` + 4 tests (URL composition included)
- [ ] `src/nodes/linear_create_issue.py` + 4 tests (GraphQL body included)
- [ ] Overall tests 77 pass (existing 65 → 77)
- [ ] Commit → push → PR

## Out of scope

- Additional actions per SaaS (Notion query, Airtable list/update, Linear update, …) — add as separate nodes when demand appears
- Streaming LLM responses — MVP supports single response only
- OAuth-based nodes (Gmail/Sheets, …) — need an OAuth credential_type design ADR (separate roadmap)
- LlmNode first-class abstraction — covered in ADR-007 alongside the Heavy path. Light is fine as a regular node.
- `Inference_Service` local Gemma 4 backend — created together with Heavy-user Agent work
