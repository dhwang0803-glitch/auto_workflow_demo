# PLAN_13 — Messaging/LLM 확장 (discord_notify + anthropic_chat)

> 선행: ADR-017 (노드 카탈로그 최소 사양) — Messaging 카테고리 3개 최소 /
> LLM 카테고리 2개 최소 기준 충족. 본 PLAN 은 PR B (2 노드).

## 목적

ADR-017 §1 에서 결정된 카테고리별 최소 수량 중:
- **Messaging (3 최소, 현 2)** → `discord_notify` 로 Slack 금지 고객군 (금융/공공) 커버
- **LLM (2 최소, 현 1)** → `anthropic_chat` 으로 벤더 다양화 및 Claude 사용 고객 대응

## 스코프

| node_type | 엔드포인트 | credential |
|---|---|---|
| `discord_notify` | `POST https://discord.com/api/webhooks/{id}/{token}` | 없음 (webhook URL 자체가 인증) |
| `anthropic_chat` | `POST https://api.anthropic.com/v1/messages` | `http_bearer` → `x-api-key` 헤더 |

## 노드 스펙

### 1. DiscordNotifyNode

Discord Incoming Webhook — Slack `slack_notify` 패턴과 구조 동일:

```
config:
  webhook_url: str
  content: str           # 메시지 본문
  username?: str         # override bot name
  timeout_seconds?: int (default 10)

response:
  status_code: int
  ok: true
```

**credential 없음** — webhook_url 자체가 secret 역할 (Slack 과 동일 패턴). 단, URL 을 credential 로 등록하고 싶으면 `slack_webhook` credential_type 재사용 가능 (ADR-017 §4 트랙 외).

### 2. AnthropicChatNode

Anthropic Messages API — OpenAI Chat Completions 와 포맷 유사하나 헤더/body 구조 상이:

```
config:
  api_token: str         # http_bearer → x-api-key 헤더 주입
  model: str             # "claude-opus-4-7" / "claude-sonnet-4-6" / "claude-haiku-4-5-20251001"
  messages: list[{role, content}]
  system?: str           # Anthropic 은 system 을 top-level 필드 (OpenAI 는 messages 내 role=system)
  max_tokens: int        # Anthropic 필수
  temperature?: float
  timeout_seconds?: int (default 60)

headers:
  x-api-key: <api_token>       # Bearer 아님
  anthropic-version: 2023-06-01
  content-type: application/json

response:
  content: str                 # content[0].text
  model: str
  stop_reason: str             # "end_turn", "max_tokens" 등
  usage: {input_tokens, output_tokens}
```

**OpenAI 와의 형식 차이 주의:**
- 인증: `Authorization: Bearer` (OpenAI) vs `x-api-key` (Anthropic)
- system 메시지: messages 배열 내 (OpenAI) vs top-level `system` (Anthropic)
- max_tokens: 선택 (OpenAI) vs 필수 (Anthropic)
- usage: `total_tokens` (OpenAI) vs `input_tokens`/`output_tokens` 분리 (Anthropic)

## 파일 변경

### 신규
| 파일 | 역할 |
|------|------|
| `src/nodes/discord_notify.py` | DiscordNotifyNode |
| `src/nodes/anthropic_chat.py` | AnthropicChatNode |
| `tests/test_discord_notify_node.py` | 단위 테스트 |
| `tests/test_anthropic_chat_node.py` | 단위 테스트 |

수정: 없음.

## 테스트 전략 (각 노드 3개, 총 6개)

httpx_mock 기반.

### test_discord_notify_node.py (3)
1. `test_discord_notify_success` — 200 응답, `{status_code, ok}` 반환
2. `test_discord_notify_sends_content_payload` — body 에 `content` 필드 정확
3. `test_discord_notify_error_raises` — 4xx → HTTPStatusError

### test_anthropic_chat_node.py (3)
1. `test_anthropic_chat_success` — 응답에서 `content[0].text`, `usage` 추출
2. `test_anthropic_chat_headers_and_body` — `x-api-key` 헤더 (Bearer 아님), `anthropic-version`, system top-level 전송
3. `test_anthropic_chat_error_raises` — 401 → HTTPStatusError

## 보안 불변식

- `api_token` 은 config 경유 평문만 수용 — 노드 내부 로깅 금지
- Discord webhook_url 은 config 경유 직접 사용 — 저장 시 credential 테이블 사용 권장이지만 현 PLAN 범위 외

## 체크리스트

- [ ] `src/nodes/discord_notify.py` + 테스트 3
- [ ] `src/nodes/anthropic_chat.py` + 테스트 3
- [ ] 전체 테스트 pass (기존 + 6)
- [ ] feature/plan-13-messaging-llm 브랜치 push
- [ ] PR → main

## Out of scope

- Discord 의 embed/attachment/reaction — content 텍스트만
- Anthropic streaming / tool use / vision — 단순 메시지만
- Claude 모델 라우팅 / 폴백 — ADR-008 의 Inference_Service 에서 다룸 (Phase 2)
- MS Teams / WeChat — 수요 확인 후 개별 노드
