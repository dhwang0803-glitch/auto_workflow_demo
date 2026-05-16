# PLAN_07 — EmailSendNode (SMTP via aiosmtplib)

> Status: DRAFT
> Branch: `Execution_Engine`
> Predecessors: PLAN_01 (BaseNode/Registry), PLAN_06 (Slack/Delay pattern)

## Purpose

Send email via SMTP from a workflow. Per the CLAUDE.md policy, the
credential value is **injected through config, used once, and dropped
when the local variable scope ends**.

## File changes

### New
| File | Role |
|------|------|
| `src/nodes/email_send.py` | EmailSendNode — SMTP send via aiosmtplib |
| `tests/test_email_send_node.py` | EmailSendNode tests |

### Modified
| File | Change |
|------|--------|
| `pyproject.toml` | Add `aiosmtplib>=3.0` dependency |

**Do not name the file `email.py`** — risk of shadowing the stdlib
`email` package. Use `email_send.py`.

## Implementation details

### EmailSendNode (`src/nodes/email_send.py`)

```python
class EmailSendNode(BaseNode):
    node_type = "email_send"

    async def execute(self, input_data, config):
        msg = EmailMessage()
        msg["From"] = config["from"]
        msg["To"] = ", ".join(config["to"])
        msg["Subject"] = config["subject"]
        msg.set_content(config["body"])
        if "body_html" in config:
            msg.add_alternative(config["body_html"], subtype="html")

        await aiosmtplib.send(
            msg,
            hostname=config["smtp_host"],
            port=config["smtp_port"],
            username=config["smtp_user"],
            password=config["smtp_password"],
            start_tls=config.get("use_starttls", True),
            timeout=config.get("timeout_seconds", 30),
        )
        return {"sent": True, "to": config["to"]}
```

- **Required config**: `smtp_host`, `smtp_port`, `smtp_user`, `smtp_password`, `from`, `to` (list), `subject`, `body`
- **Optional config**: `body_html`, `use_starttls` (default True), `timeout_seconds` (default 30)
- **Returns**: `{"sent": True, "to": [...]}` — on failure `aiosmtplib.SMTPException` propagates (the executor records it as failed)
- `to` is a list. Serialize the header with `", ".join()`.
- The credential exists only as a function-local — eligible for GC at call end (the node is stateless).

## Test strategy

Patch `aiosmtplib.send` with an AsyncMock. Verify call arguments without
a real SMTP connection.

### test_email_send_node.py (4)
1. `test_email_send_success` — send with only required config, send() called once, return sent=True
2. `test_email_send_passes_credentials` — send() call args carry hostname/port/username/password exactly
3. `test_email_send_with_html_body` — when body_html is set, the EmailMessage is built as multipart
4. `test_email_send_smtp_error_propagates` — aiosmtplib.SMTPException → propagates to the caller

## Dependency addition

```toml
dependencies = [
    "httpx>=0.27",
    "celery[redis]>=5.3",
    "websockets>=12.0",
    "RestrictedPython>=7.0",
    "aiosmtplib>=3.0",
    "auto-workflow-database",
]
```

## Checklist

- [ ] `src/nodes/email_send.py` — EmailSendNode + registry registration
- [ ] `pyproject.toml` — add aiosmtplib
- [ ] `tests/test_email_send_node.py` — 4 tests
- [ ] Keep overall tests at 33→37
- [ ] Commit → push → PR

## Follow-ups

- DB Query node — separate PLAN with an accompanying security ADR (SQL-injection prevention policy)
- Standardize the credential-injection flow — currently injected directly via config; consider a `credential_id` reference scheme later
