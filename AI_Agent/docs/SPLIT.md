# API_Server ↔ AI_Agent 분할 스펙

> 본 문서는 PLAN_11 작성 **전** 합의된 분할 설계를 고정한다.
> 배경 결정: 2026-04-22 세션. 본 문서가 PLAN_11 의 전제 조건.

## 1. 배경

PLAN_02 (AI Composer) 는 `API_Server/app/services/ai_composer_service.py` 단일 파일에
LLM 백엔드 + 프롬프트 + 스트림 파서 + rate limiter + 노드 카탈로그 로더까지 모두 담고
있다 (671 LOC). PLAN_11 (해커톤) 에서 아래가 추가된다:

- `LlamaCppGemmaBackend` + Dockerfile 내 `llama-server` 서브프로세스
- `EmbeddingBackend` (Gemma E2B pooling + BGE-M3 fallback)
- 노드 카탈로그 RAG (임베딩 인덱스 + 검색)
- 데모 시나리오 fixture + 프롬프트 템플릿 확장

이것을 전부 `API_Server` 에 추가하면 AI 의존성(llama-cpp, GPU runtime, HF Hub,
임베딩 모델) 이 워크플로우 CRUD 서버의 배포 단위에 묶인다. 분리하지 않으면
`API_Server` Cloud Run 서비스가 GPU 컨테이너에 종속된다.

## 2. 배포 옵션 (확정: A)

| 옵션 | 구조 | 해커톤 선택 |
|---|---|---|
| **A** | `AI_Agent` 단일 Cloud Run GPU 컨테이너. `llama-server` 를 Python FastAPI 와 같은 컨테이너에서 서브프로세스로 임베드. 콜드스타트 1회. | ✅ |
| B | AI_Agent (CPU) + `llama.cpp-server` (GPU) 2개 서비스 분리. | 해커톤 후 확장 필요 시 고려 |

## 3. 이동 매핑

### 3.1 API_Server → AI_Agent (이동)

| 현재 위치 | 심볼 | AI_Agent 신규 위치 |
|---|---|---|
| `API_Server/app/services/ai_composer_service.py:69` | `LLMBackend` Protocol | `AI_Agent/app/backends/protocols.py` |
| `…:97` | `AnthropicBackend` | `AI_Agent/app/backends/anthropic.py` |
| `…:158` | `StubLLMBackend` | `AI_Agent/app/backends/stub.py` |
| `…:304-333` | `RationaleDelta` / `Result` / `StreamError` | `AI_Agent/app/models/compose.py` |
| `…:334` | `_RationaleStreamParser` | `AI_Agent/app/services/stream_parser.py` |
| `…:446` | `AIComposerService` (오케스트레이션 로직) | `AI_Agent/app/services/compose_service.py` |
| `…:645` | `build_node_catalog_provider` | `AI_Agent/app/catalog/provider.py` |
| `API_Server/tests/test_ai_composer.py` (583 LOC) | 백엔드·서비스·파서 테스트 | `AI_Agent/tests/` (쪼개어 분배) |
| `API_Server/app/config.py:77` | `ai_composer_use_stub` | AI_Agent 환경변수 `LLM_BACKEND` 로 통합 |

### 3.2 API_Server 잔류

| 위치 | 심볼 | 이유 |
|---|---|---|
| `app/routers/ai_composer.py` (100 LOC) | SSE 라우터 | 공개 트래픽·인증 책임 |
| `ai_composer_service.py:45,59` | `ComposerDisabledError`, `ComposerRateLimitError` | 프록시 레벨에서 raise |
| `ai_composer_service.py:406` | `_InMemoryRateLimiter` | rate limit 은 트래픽 층 책임 |
| `config.py:81-82` | `ai_compose_rate_per_minute`, `ai_compose_max_tokens` | rate limiter 파라미터 |

**에러 타입 split 주의**: `InvalidComposerResponseError` (line 52) 는 파서가 raise
하므로 **AI_Agent 측**이 1차. API_Server 는 HTTP 422 로 번역해 클라이언트에 전달
(세부는 PLAN_11 PR 0 에서 확정).

### 3.3 API_Server 신규

| 위치 | 심볼 | 역할 |
|---|---|---|
| `API_Server/app/services/ai_agent_client.py` | `AIAgentHTTPBackend` | AI_Agent 의 `/v1/compose` 호출하는 얇은 httpx 클라이언트. 기존 `LLMBackend` DI 자리를 차지 |
| `API_Server/app/config.py` | `ai_agent_base_url`, `ai_agent_timeout_s`, `ai_agent_auth_audience` (Cloud Run IAM ID token audience) | AI_Agent 서비스 접속 정보 |

## 4. HTTP 경계 (API_Server → AI_Agent)

엔드포인트 3개. 세부 스키마·SSE 프레임 포맷은 **PLAN_11 PR 0 에서 계약 확정**.

| 메서드 | 경로 | 역할 |
|---|---|---|
| POST | `/v1/compose` | 자연어 + 컨텍스트 → WorkflowSchema. SSE 스트림 (Anthropic `message_start`/`content_block_delta` 호환성 유지 검토) |
| POST | `/v1/embed` | 텍스트 배열 → 벡터 배열 (노드 검색 업데이트) |
| GET  | `/v1/health` | 모델 ready 여부. Cloud Run startup/liveness probe 에 사용 |

**인증**: Cloud Run IAM invoker (서비스 계정 ID token). 외부 공개 금지.

## 5. PLAN_11 영향

### 5.1 PR 재배치 (분할 전 제안 대비)

| 분할 전 | 분할 후 |
|---|---|
| W1 PR 1 (API_Server) — llama.cpp smoke + `LlamaCppGemmaBackend` | **AI_Agent** PR — 백엔드 구현 + Dockerfile 골격 |
| W1 PR 2 (API_Server) — `EmbeddingBackend` Protocol + Gemma E2B pooling | **AI_Agent** PR — 임베딩 백엔드 + pooling |
| W1 PR 3 (API_Server) — 품질 A/B 기록 | **AI_Agent** PR — A/B 테스트 + 기본 백엔드 스위치 |
| (없음) | **API_Server** PR — `AIAgentHTTPBackend` 프록시 + PLAN_02 심볼 이동 |
| W2 PR 4 (infra) — Cloud Run GPU 배포 | **infra** PR — AI_Agent Cloud Run GPU 서비스 (option A) |

### 5.2 일정 임팩트

- W1 초반 **0.5-1일 추가 소요**: AI_Agent 디렉토리 신설 완료분 외에, 기존 심볼
  이동 + API_Server 측 HTTP 클라이언트 + 테스트 분리 작업.
- 상쇄 이점: PLAN_11 이후 모든 AI 기능 확장이 AI_Agent 내부로 격리. API_Server
  는 프록시만 관리.
- 리스크: HTTP 계약 미확정 시 양쪽 PR 동시 진행 블로킹 — **PLAN_11 PR 0 (HTTP
  계약 + 이동 복사) 을 최우선 1순위**로 배치.

### 5.3 마이그레이션 순서 (PLAN_11 PR 0 후보)

1. AI_Agent 에 `AnthropicBackend` / `StubLLMBackend` / `_RationaleStreamParser` /
   `AIComposerService` 핵심 로직을 **복사** (아직 API_Server 쪽 삭제하지 않음).
2. AI_Agent `/v1/compose` HTTP API 기동, 로컬에서 기존 pytest 재사용해 동작 확인.
3. API_Server 에 `AIAgentHTTPBackend` 구현, DI 에서 `LLMBackend` 구현체를 이것으로 교체.
4. 기존 `API_Server/tests/test_ai_composer.py` pass 확인 후 API_Server 쪽
   중복 심볼 제거.
5. `LLMBackend` Protocol 정의도 AI_Agent 로 이관. API_Server 는 HTTP 클라이언트
   인터페이스만 남김.

복사 → 전환 → 삭제 순서는 any-time rollback 을 위해.

## 6. 관련 참조

- auto-memory `project_gemma4_hackathon.md` — 해커톤 배경·상금·평가 비중
- auto-memory `project_gemma4_model_decisions.md` — 26B-A4B Q4 GGUF + llama.cpp
- auto-memory `project_llm_backend_swap_plan.md` — 본 분할 반영 필요 (백엔드 교체는 AI_Agent 내부 작업으로 재정의)
- 후속: `AI_Agent/plans/PLAN_11_HACKATHON_SUBMISSION.md` (작성 예정)
