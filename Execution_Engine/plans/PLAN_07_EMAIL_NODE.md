# PLAN_07 — EmailSendNode (SMTP via aiosmtplib)

> 상태: DRAFT
> 브랜치: `Execution_Engine`
> 선행: PLAN_01 (BaseNode/Registry), PLAN_06 (Slack/Delay 패턴 참고)

## 목적

워크플로우에서 SMTP 서버를 통해 이메일 전송. 자격증명은 CLAUDE.md 방침대로
**config 로 주입된 값을 1회 사용 후 지역 변수 범위에서 자동 폐기**.

## 파일 변경

### 신규
| 파일 | 역할 |
|------|------|
| `src/nodes/email_send.py` | EmailSendNode — aiosmtplib 기반 SMTP 전송 |
| `tests/test_email_send_node.py` | EmailSendNode 테스트 |

### 수정
| 파일 | 변경 |
|------|------|
| `pyproject.toml` | `aiosmtplib>=3.0` 의존성 추가 |

**파일명 `email.py` 금지** — stdlib `email` 패키지와 shadowing 위험. `email_send.py` 사용.

## 구현 상세

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

- **필수 config**: `smtp_host`, `smtp_port`, `smtp_user`, `smtp_password`, `from`, `to` (list), `subject`, `body`
- **옵션 config**: `body_html`, `use_starttls` (default True), `timeout_seconds` (default 30)
- **반환**: `{"sent": True, "to": [...]}` — 실패 시 `aiosmtplib.SMTPException` 전파 (executor 가 failed 로 기록)
- `to` 는 리스트. `", ".join()` 으로 헤더 직렬화.
- 자격증명은 함수 지역 변수로만 존재 → 호출 종료 시 GC 대상 (노드는 stateless).

## 테스트 전략

aiosmtplib.send 는 AsyncMock 으로 패치. 실제 SMTP 연결 없이 호출 인자 검증.

### test_email_send_node.py (4)
1. `test_email_send_success` — 필수 config 만으로 전송, send() 호출 1회, 반환 sent=True
2. `test_email_send_passes_credentials` — send() 호출 인자에 hostname/port/username/password 정확히 전달
3. `test_email_send_with_html_body` — body_html 지정시 EmailMessage 가 multipart 로 구성됨
4. `test_email_send_smtp_error_propagates` — aiosmtplib.SMTPException → 호출측으로 전파

## 의존성 추가

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

## 체크리스트

- [ ] `src/nodes/email_send.py` — EmailSendNode + registry 등록
- [ ] `pyproject.toml` — aiosmtplib 추가
- [ ] `tests/test_email_send_node.py` — 4 tests
- [ ] 전체 33→37 테스트 유지
- [ ] 커밋 → push → PR

## 후속 작업

- DB Query 노드 — 별도 PLAN, 보안 결정 (SQL 인젝션 방지 정책) ADR 동반
- 자격증명 주입 흐름 표준화 — 현재는 config 직접 주입, 향후 `credential_id` 참조 방식 검토
