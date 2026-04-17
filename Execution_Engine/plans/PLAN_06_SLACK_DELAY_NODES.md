# PLAN_06 — SlackNotifyNode + DelayNode

> 상태: DRAFT
> 브랜치: `Execution_Engine`
> 선행: PLAN_01 (BaseNode/Registry), PLAN_05 (Condition/Code)

## 목적

워크플로우 자동화의 기본 빌딩블록 확장:

- **SlackNotifyNode** — Incoming Webhook URL로 알림 전송
- **DelayNode** — 후속 노드 실행 전 일정 시간 대기

둘 다 외부 의존성이 기존 스택(`httpx`, `asyncio`)에 포함되어 있어 신규 의존성 없이 추가.

## 파일 변경

### 신규
| 파일 | 역할 |
|------|------|
| `src/nodes/slack.py` | SlackNotifyNode — Incoming Webhook POST |
| `src/nodes/delay.py` | DelayNode — asyncio.sleep 기반 지연 |
| `tests/test_slack_node.py` | SlackNotifyNode 테스트 |
| `tests/test_delay_node.py` | DelayNode 테스트 |

### 수정
없음 (신규 의존성 없음).

## 구현 상세

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

- **필수 config**: `webhook_url`, `text`
- **옵션 config**: `timeout_seconds` (default 10)
- **반환**: `{"status_code": int, "ok": True}` — 실패 시 `HTTPStatusError` 전파 (executor가 failed로 기록)
- Webhook URL 은 채널이 이미 고정되어 있으므로 channel override 지원하지 않음 (YAGNI)

### 2. DelayNode (`src/nodes/delay.py`)

```python
class DelayNode(BaseNode):
    node_type = "delay"

    async def execute(self, input_data, config):
        seconds = config["seconds"]
        await asyncio.sleep(seconds)
        return {"waited_seconds": seconds}
```

- **필수 config**: `seconds` (int or float)
- **반환**: `{"waited_seconds": seconds}`
- 타임아웃 상한은 executor 레이어에서 관리 — 노드 자체는 정책 없음

## 테스트 전략

### test_slack_node.py (3)
1. `test_slack_notify_success` — `httpx_mock` 200 → result ok=True
2. `test_slack_notify_error_raises` — 500 응답 → `HTTPStatusError`
3. `test_slack_notify_sends_text_payload` — POST body에 `{"text": ...}` 포함

### test_delay_node.py (2)
1. `test_delay_waits` — 0.05초 지연, elapsed >= 0.05
2. `test_delay_returns_waited_seconds` — 반환값 확인

## 체크리스트

- [ ] `src/nodes/slack.py` — SlackNotifyNode + registry 등록
- [ ] `src/nodes/delay.py` — DelayNode + registry 등록
- [ ] 테스트 5개 작성 + pass
- [ ] 전체 28→33 테스트 유지
- [ ] 커밋 → push → PR

## 후속 작업

- Email 노드 (SMTP 또는 SendGrid API, 자격증명 연동)
- DB Query 노드 (자격증명 + SQL 인젝션 방지)
- 추가 조건/반복 노드 확장
