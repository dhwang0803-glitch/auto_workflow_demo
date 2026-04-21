# PLAN_02 — AI Composer (Frontend + API_Server)

> **브랜치**: `Frontend` + `API_Server` 연동 · **작성일**: 2026-04-21 · **상태**: Draft
>
> 사용자가 자연어로 의도를 입력하면 AI 가 노드 카탈로그를 선택·연결하여
> 워크플로 초안(DAG draft) 을 생성한다. 한 번에 완성하기보다
> **clarification 대화 → draft → diff 기반 iterative refinement** 흐름을
> 따른다. PLAN_01 에디터 위에 얹는 레이어이며, draft 는 항상 에디터에
> 렌더링되어 사용자가 수동으로 수정 가능하다.

## 1. 목표

1. 자연어 입력 패널 (좌측 Chat sidebar) — 에디터와 분리된 레이아웃
2. **Clarification 단계** — 모호한 스펙은 LLM 이 먼저 질문 (데이터 소스 / 주소록 / 템플릿 등)
3. **DAG draft 생성** — 스펙 확정 후 LLM 이 노드 카탈로그 내에서 선택 + 연결 + config 힌트 생성
4. **에디터 주입** — draft 를 React Flow store 에 load, 사용자 수정 가능
5. **Iterative refinement** — "이 부분 바꿔줘" → LLM 이 diff 를 반환 → 에디터에 부분 적용
6. **Rationale SSE 스트리밍** — LLM 의 설명(rationale) 만 토큰 단위로 chat 버블에 타이핑 효과. DAG/diff 는 완성 후 일괄 전송
7. **Agent 통합** — 시스템 Agent 1종 (`composer-agent`) 으로 등록, 기존 Agent_Management 와 일관

## 2. 범위

**In**
- `API_Server/app/routers/ai_composer.py` — 자연어 → DAG 생성 REST 엔드포인트 + SSE 스트리밍 엔드포인트
- `API_Server/app/services/ai_composer_service.py` — Claude API 호출 (stream=True) + 노드 카탈로그 컨텍스트 주입 + 프롬프트 관리
- Frontend `ChatPanel` — 자연어 입력 + clarification 대화 UI + rationale 타이핑 효과
- Frontend `loadFromJson` store action (PLAN_01 에서 노출해 둔 훅) 재사용
- Diff 뷰어 — 기존 DAG vs 제안된 DAG 의 노드/엣지 변경점 시각화
- 프롬프트 템플릿 — 노드 카탈로그 + config schema 를 system prompt 에 포함
- **SSE 파서** — Frontend `fetch` + `ReadableStream` 으로 `rationale` 토큰 청크 수신 → chat 버블 append
- 테스트:
  - API_Server: Claude API mock 으로 composer_service 단위 테스트 (스트림/non-stream 양쪽)
  - Frontend: Playwright 로 "자연어 입력 → rationale 스트리밍 → draft 수신 → 캔버스 렌더" E2E 1건

**Out (후속 PLAN)**
- LLM 다양화 (OpenAI/Gemini) — MVP 는 Claude 4.7 만
- 사용자별 템플릿 학습/저장 — PLAN_11 Template System
- Fine-tuning / 임베딩 기반 노드 추천 — 향후
- 실행 로그 기반 자가 수정 (실패 시 LLM 이 DAG 고침) — 향후
- 음성 입력 — 향후

## 3. 핵심 시나리오 (사용자 스토리)

> 사용자: "어제 한국 주식시장 시황과 뉴스를 가져와서 docs 로 보고서 만들고 임원진에게 gmail 로 보내줘"

**Step 1 — Clarification (LLM 이 질문)**
- "주식 데이터 소스는 어디를 쓸까요? (KRX 공식 API / Yahoo Finance / 네이버 금융)"
- "임원진 주소록은 Gmail contacts 에서 그룹으로 관리되나요, 아니면 수동으로 입력하시나요?"
- "Docs 보고서 양식이 있나요? 없으면 기본 템플릿을 제안드릴게요."

**Step 2 — DAG draft (사용자 답변 후)**
```
[http_request: Yahoo Finance 어제 한국 지수]
[http_request: 뉴스 API (네이버 또는 Google News)]
  → [anthropic_chat: 두 입력 요약 → 보고서 본문 생성]
    → [google_docs_append_text: 신규 문서 생성 + 본문 기입]
      → [gmail_send: to=임원진 목록, subject="...", body=문서 링크]
```

**Step 3 — 에디터 주입**
- draft 가 React Flow 캔버스에 렌더됨 (자동 레이아웃)
- 사용자는 노드 속성을 직접 편집 가능 (예: 수신자 이메일 입력)
- 미완성 config 는 **노란색 배지** 로 표시 (placeholder 값들)

**Step 4 — Iterative refinement**
- 사용자: "Docs 말고 Slides 로 만들어줘"
- LLM: 해당 노드만 `google_slides_create_presentation` 으로 교체하는 diff 반환
- Frontend: diff 를 highlight 한 후 **Accept/Reject** 버튼

## 4. 백엔드 API 사양

### `POST /api/v1/ai/compose` (SSE 스트리밍)

**Request**
```json
{
  "session_id": "uuid (optional — 첫 요청 시 서버가 생성)",
  "message": "어제 주식 시황...",
  "current_dag": { "nodes": [...], "edges": [...] } | null
}
```

**Response** — `Content-Type: text/event-stream`. 이벤트 3종:

```
event: rationale_delta
data: {"token": "이 "}

event: rationale_delta
data: {"token": "워크플로는 "}

... (토큰 누적) ...

event: result
data: {
  "session_id": "...",
  "intent": "draft | clarify | refine",
  "clarify_questions": ["...", "..."] | null,
  "proposed_dag": { "nodes": [...], "edges": [...] } | null,
  "diff": {
    "added_nodes": [...],
    "removed_node_ids": [...],
    "modified_nodes": [...]
  } | null,
  "rationale": "전체 누적 rationale (delta 합산 검증용)"
}

event: error
data: {"code": "rate_limit_exceeded", "message": "..."}
```

**스트리밍 전략**:
- Anthropic SDK `stream=True` + 도구 사용 또는 JSON 응답
- **rationale 만 먼저 발화하도록 프롬프트 유도** — 모델이 `<rationale>...</rationale>` 블록을 먼저 출력하게 하고, 그 사이 토큰을 `rationale_delta` 로 방출
- `<rationale>` 닫힌 이후 토큰은 JSON 버퍼에 누적 → 완성되면 파싱 후 `result` 이벤트로 일괄 전송
- DAG/diff 파싱 실패 시 `error` 이벤트 + 스트림 종료

**Non-stream fallback**: 테스트/debugging 용 `?stream=false` 쿼리 플래그 지원. 이 경우 기존 JSON 응답 한 번으로 반환

- **Stateful session**: `session_id` 로 대화 이력 유지 (Redis, Memorystore 재사용)
- **Rate limit**: 유저당 분당 10회 (LLM 비용 보호)
- **인증**: 기존 `Depends(get_current_user)` 재사용
- **취소**: 클라이언트가 연결 끊으면 서버는 `asyncio.CancelledError` 처리 후 Anthropic 스트림 close

### 노드 카탈로그 컨텍스트 크기

- 30+ 노드 × 평균 1KB (schema + description) = ~30KB → 시스템 프롬프트에 직접 포함 가능
- 확장 시 (100+ 노드) 임베딩 기반 RAG 로 전환 고려 — 별도 PLAN

## 5. 프롬프트 구조

```
[SYSTEM]
너는 워크플로 자동화 에이전트다. 아래 노드 카탈로그 안에서만 선택할 수 있다.
사용자의 요청이 모호하면 DAG 를 만들기 전에 질문하라.
출력 형식은 JSON Schema 에 맞춰야 한다. (intent, clarify_questions, proposed_dag, diff, rationale)

<node_catalog>
{json_dump_of_catalog}
</node_catalog>

<current_dag>
{user's current workflow or null}
</current_dag>

[USER]
{사용자 메시지}

[ASSISTANT — JSON]
```

- **Prompt caching** 활용 (Anthropic SDK `cache_control`) — 노드 카탈로그는 세션 내 불변이므로 캐시
- `max_tokens` 적당히 제한 (4k) — DAG 는 보통 10개 이하 노드

## 6. Frontend 구조

- `src/components/ChatPanel.tsx` — 좌측 250px 고정 패널 (토글 가능)
- `src/lib/composer.ts` — `/api/v1/ai/compose` SSE 클라이언트
  - `fetch` + `ReadableStream` + `TextDecoder` 로 `event: ...` / `data: ...` 프레임 파싱
  - `rationale_delta` → chat 버블에 incremental append
  - `result` → DAG/diff 수신 콜백 호출
  - `error` → 토스트 + 스트림 종료
  - 취소는 `AbortController.abort()` — 사용자가 stop 버튼 클릭 시
- `src/store/composer.ts` — Zustand slice (session_id, messages, streaming_rationale, pending_diff)
- Clarification 질문은 **chat 버블** 로 표시, 사용자 답변은 자유 텍스트
- Rationale 타이핑 중엔 버블 끝에 **커서 깜빡임** (UX 명시적 신호)
- DAG draft 수신 시: `editorStore.loadFromJson(proposed_dag)` + 자동 레이아웃
- Diff 수신 시: diff 노드를 **초록(추가)/빨강(제거)/노랑(수정)** 하이라이트 + Accept/Reject 버튼

## 7. 보안 / 비용 가드레일

- **LLM 호출은 반드시 백엔드 경유** — 프론트엔드에서 Claude API 키 직접 노출 금지
- **Anthropic API 키는 Secret Manager** — 기존 credential 저장소 재사용 X (사용자 자격증명과 섞지 않음, 운영자 소유)
- **Rate limit**: 유저당 분당 10회, 일간 200회 (환경변수 override 가능)
- **Cost telemetry**: 각 호출의 `input_tokens / output_tokens` 로그 → Cloud Logging
- **Prompt injection 방지**: 사용자 메시지를 시스템 프롬프트가 아닌 user role 로만 전달 + JSON schema 엄격 검증

## 8. 수용 기준

- [ ] `POST /api/v1/ai/compose` SSE 엔드포인트 동작 (Claude API mock 으로 테스트)
- [ ] `?stream=false` fallback 단일 JSON 응답 동작
- [ ] 사용자 메시지 → intent=`clarify` 응답 시나리오 통과
- [ ] 사용자 메시지 → intent=`draft` 응답 + 유효한 DAG (서버 측 `dag_validator` 통과)
- [ ] `current_dag` 있는 상태 → intent=`refine` + diff 생성
- [ ] SSE `rationale_delta` 이벤트 다수 수신 후 `result` 이벤트 1회 수신
- [ ] 클라이언트 `AbortController.abort()` → 서버 측 Anthropic 스트림 close 확인
- [ ] Frontend ChatPanel 에서 입력 → rationale 타이핑 효과 → DAG 가 캔버스에 렌더
- [ ] Diff 수신 시 Accept 누르면 에디터에 반영, Reject 누르면 무시
- [ ] 빈 사용자 메시지 400 / 인증 없으면 401
- [ ] Rate limit 초과 시 429 (SSE 스트림 시작 전 HTTP 헤더로 응답)

## 9. 선결 질문

1. **Session store** — MVP in-memory (Python dict) vs Redis. in-memory 는 Cloud Run 스케일 아웃 시 세션 분실. **채택: Redis (이미 Memorystore 가 있음)** — ADR-021 Memorystore 재사용
2. **SSE 스트리밍 범위** — DAG/diff 까지 스트리밍하면 부분 JSON 파싱 이슈 → **rationale 만 스트리밍, DAG/diff 는 완성 후 일괄 전송** 로 확정 (2026-04-21). 공수: 백엔드 +0.5일, 프론트 +1일
3. **LLM 호출 실패 fallback** — 타임아웃/rate limit 시 사용자에게 "나중에 재시도" 메시지 + 에디터는 무변경
4. **Agent Framework 와의 관계** — 기존 `agents` 테이블에 `composer-agent` 를 시스템 agent 로 등록하는 게 맞을지, 아니면 별도 서비스인지. **채택: 별도 서비스** — composer 는 유저 VPC 실행 대상이 아니므로 Agent 테이블에 섞지 않음 (다만 서비스명은 `composer-agent` 로 명명 일관성 유지)

## 10. 후속 영향

- **PLAN_07 Credentials UI** — AI 가 생성한 DAG 에 `secret_ref` 필드가 있으면 사용자가 Credentials UI 로 연결해야 함. 의존 관계 명시
- **PLAN_11 Template System** — 사용자가 자주 쓰는 composer 결과물을 템플릿으로 저장. AI 가 과거 템플릿을 우선 추천
- **Cost Guard** — Anthropic 비용 상한 일간 $X 초과 시 자동 차단 운영 스위치 필요. ADR 신설 후보
- **ADR-022 (Frontend 스택)** 와 별개로 **ADR-023 (AI Composer)** 신설 검토 — LLM 종속성은 아키텍처 결정 사항
