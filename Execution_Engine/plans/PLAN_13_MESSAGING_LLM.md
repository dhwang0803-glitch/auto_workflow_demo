# PLAN_13 — Messaging/LLM expansion (discord_notify + anthropic_chat)

> Predecessor: ADR-017 (node-catalog minimum spec) — meets the Messaging
> category's 3-node minimum / LLM category's 2-node minimum. This PLAN
> is PR B (2 nodes).

## Purpose

Among ADR-017 §1's category minimums:
- **Messaging (3 min, currently 2)** → cover customers that ban Slack (finance/public sector) with `discord_notify`
- **LLM (2 min, currently 1)** → vendor diversification + support for Claude customers via `anthropic_chat`

## Scope

| node_type | Endpoint | credential |
|---|---|---|
| `discord_notify` | `POST https://discord.com/api/webhooks/{id}/{token}` | none (the webhook URL itself authenticates) |
| `anthropic_chat` | `POST https://api.anthropic.com/v1/messages` | `http_bearer` → `x-api-key` header |

## Node specs

### 1. DiscordNotifyNode

Discord Incoming Webhook — same structural pattern as Slack `slack_notify`:

```
config:
  webhook_url: str
  content: str           # message body
  username?: str         # override bot name
  timeout_seconds?: int (default 10)

response:
  status_code: int
  ok: true
```

**No credential** — the webhook_url itself is the secret (same pattern
as Slack). If you do want the URL registered as a credential, the
`slack_webhook` credential_type can be reused (outside ADR-017 §4
track).

### 2. AnthropicChatNode

Anthropic Messages API — similar format to OpenAI Chat Completions, but
the header / body structure differs:

```
config:
  api_token: str         # http_bearer → injected into x-api-key header
  model: str             # "claude-opus-4-7" / "claude-sonnet-4-6" / "claude-haiku-4-5-20251001"
  messages: list[{role, content}]
  system?: str           # Anthropic puts system at the top level (OpenAI puts it inside messages as role=system)
  max_tokens: int        # required for Anthropic
  temperature?: float
  timeout_seconds?: int (default 60)

headers:
  x-api-key: <api_token>       # not Bearer
  anthropic-version: 2023-06-01
  content-type: application/json

response:
  content: str                 # content[0].text
  model: str
  stop_reason: str             # "end_turn", "max_tokens", etc.
  usage: {input_tokens, output_tokens}
```

**Watch the format differences with OpenAI:**
- Auth: `Authorization: Bearer` (OpenAI) vs `x-api-key` (Anthropic)
- system message: inside messages array (OpenAI) vs top-level `system` (Anthropic)
- max_tokens: optional (OpenAI) vs required (Anthropic)
- usage: `total_tokens` (OpenAI) vs separate `input_tokens` / `output_tokens` (Anthropic)

## File changes

### New
| File | Role |
|------|------|
| `src/nodes/discord_notify.py` | DiscordNotifyNode |
| `src/nodes/anthropic_chat.py` | AnthropicChatNode |
| `tests/test_discord_notify_node.py` | Unit tests |
| `tests/test_anthropic_chat_node.py` | Unit tests |

Modified: none.

## Test strategy (3 per node, 6 total)

httpx_mock based.

### test_discord_notify_node.py (3)
1. `test_discord_notify_success` — 200 response, returns `{status_code, ok}`
2. `test_discord_notify_sends_content_payload` — body's `content` field is exact
3. `test_discord_notify_error_raises` — 4xx → HTTPStatusError

### test_anthropic_chat_node.py (3)
1. `test_anthropic_chat_success` — extract `content[0].text`, `usage` from the response
2. `test_anthropic_chat_headers_and_body` — `x-api-key` header (not Bearer), `anthropic-version`, system sent at the top level
3. `test_anthropic_chat_error_raises` — 401 → HTTPStatusError

## Security invariants

- `api_token` is accepted only as plaintext via config — no node-internal logging
- Discord `webhook_url` is used directly via config — when persisted, using the credential table is recommended, but that's outside this PLAN's scope

## Checklist

- [ ] `src/nodes/discord_notify.py` + 3 tests
- [ ] `src/nodes/anthropic_chat.py` + 3 tests
- [ ] All tests pass (existing + 6)
- [ ] Push the feature/plan-13-messaging-llm branch
- [ ] PR → main

## Out of scope

- Discord embed/attachment/reaction — content text only
- Anthropic streaming / tool use / vision — plain messages only
- Claude model routing / fallback — handled in ADR-008's Inference_Service (Phase 2)
- MS Teams / WeChat — separate nodes after demand validation
