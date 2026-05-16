# PLAN_06 — SlackNotifyNode + DelayNode

> Status: DRAFT
> Branch: `Execution_Engine`
> Predecessors: PLAN_01 (BaseNode/Registry), PLAN_05 (Condition/Code)

## Purpose

Expand the basic workflow-automation building blocks:

- **SlackNotifyNode** — send a notification via an Incoming Webhook URL
- **DelayNode** — wait a specified duration before running downstream nodes

Both keep external dependencies inside the existing stack (`httpx`,
`asyncio`), so no new dependencies are added.

## File changes

### New
| File | Role |
|------|------|
| `src/nodes/slack.py` | SlackNotifyNode — Incoming Webhook POST |
| `src/nodes/delay.py` | DelayNode — asyncio.sleep-based wait |
| `tests/test_slack_node.py` | SlackNotifyNode tests |
| `tests/test_delay_node.py` | DelayNode tests |

### Modified
None (no new dependencies).

## Implementation details

### 1. SlackNotifyNode (`src/nodes/slack.py`)

```python
class SlackNotifyNode(BaseNode):
    node_type = "slack_notify"

    async def execute(self, input_data, config):
        webhook_url = config["webhook_url"]
        text = config["text"]
        timeout = config.get("timeout_seconds", 10)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(webhook_url, json={"text": text})
            resp.raise_for_status()
            return {"status_code": resp.status_code, "ok": True}
```

- **Required config**: `webhook_url`, `text`
- **Optional config**: `timeout_seconds` (default 10)
- **Returns**: `{"status_code": int, "ok": True}` — on failure `HTTPStatusError` propagates (the executor records it as failed)
- Webhook URL pins the channel already, so we don't support a channel override (YAGNI)

### 2. DelayNode (`src/nodes/delay.py`)

```python
class DelayNode(BaseNode):
    node_type = "delay"

    async def execute(self, input_data, config):
        seconds = config["seconds"]
        await asyncio.sleep(seconds)
        return {"waited_seconds": seconds}
```

- **Required config**: `seconds` (int or float)
- **Returns**: `{"waited_seconds": seconds}`
- The upper timeout bound is managed at the executor layer — the node itself has no policy

## Test strategy

### test_slack_node.py (3)
1. `test_slack_notify_success` — `httpx_mock` 200 → result ok=True
2. `test_slack_notify_error_raises` — 500 response → `HTTPStatusError`
3. `test_slack_notify_sends_text_payload` — POST body contains `{"text": ...}`

### test_delay_node.py (2)
1. `test_delay_waits` — 0.05s delay, elapsed >= 0.05
2. `test_delay_returns_waited_seconds` — verify the return value

## Checklist

- [ ] `src/nodes/slack.py` — SlackNotifyNode + registry registration
- [ ] `src/nodes/delay.py` — DelayNode + registry registration
- [ ] Write 5 tests + pass
- [ ] Keep overall tests at 28→33
- [ ] Commit → push → PR

## Follow-ups

- Email node (SMTP or SendGrid API, credential integration)
- DB Query node (credential + SQL-injection prevention)
- Additional conditional/loop node expansions
