# AI_Agent — Claude Code 브랜치 지침

> 루트 `CLAUDE.md` 보안 규칙과 함께 적용된다.

## 관련 문서

- 상류 의존: `API_Server` — AI 기능의 공개 엔드포인트·인증·rate limit·SSE 프록시
- 다운스트림: Modal (외부 GPU 호스팅, 2026-04-24 피벗) + `infra/` (Modal Secret 동기화용 GCP Secret Manager 만 보존)

## 모듈 역할

**AI Orchestration Service** — auto_workflow_demo 의 AI 두뇌.
LLM 추론(Gemma 4 via llama.cpp) + 임베딩(personalized retrieval) + 프롬프트 구성 +
노드 카탈로그 RAG 를 API_Server 로부터 HTTP 경계로 분리해 담는다.

API_Server 는 AI 기능의 **엔드포인트·인증·rate limit·SSE 프록시**만 담당하고,
실제 LLM/임베딩 호출과 프롬프트 오케스트레이션은 전부 이 모듈에서 수행한다.

**배포 단위**: Modal (L4 GPU, per-second 과금, scale-to-zero). `scripts/modal_app.py` 가
기존 `Dockerfile` 을 그대로 사용해 이미지 빌드 + Modal Volume 에 GGUF 캐시 + `@modal.enter()`
에서 `llama-server` 서브프로세스 부팅 + `@modal.asgi_app()` 으로 FastAPI 노출. Bearer 토큰
(env `AGENT_BEARER_TOKEN`, Modal Secret 주입) 으로 /v1/* 게이팅. 콜드스타트 1-2min
(이미지 캐시 + 모델 mmap) — 심사 기간 동안 min=0 유지.

**피벗 배경**: 2026-04-24 GCP 인프라 누적 차단 (Cloud Run GPU 쿼터 미할당 → GCE L4 spot
us-central1 전 zone capacity 부족 → on-demand `GPUS_ALL_REGIONS=0`) 으로 Modal 외부
호스팅으로 피벗. Special Tech (llama.cpp) 트랙 자격 유지.

## 파일 위치 규칙 (MANDATORY)

```
AI_Agent/
├── app/
│   ├── backends/   ← LLM/Embedding Protocol + 구현체
│   │   ├── protocols.py      ← LLMBackend, EmbeddingBackend Protocol
│   │   ├── llamacpp_gemma.py ← Gemma 4 via llama-server HTTP
│   │   ├── gemma_embedding.py← Gemma 4 E2B pooling 임베딩
│   │   ├── anthropic.py      ← dev/fallback (API_Server 에서 이동 예정)
│   │   └── stub.py           ← 로컬 테스트용 stub
│   ├── services/   ← compose 오케스트레이션, RAG, 프롬프트 조립
│   │   └── compose_service.py
│   ├── prompts/    ← 프롬프트 템플릿 (Jinja 또는 f-string)
│   ├── catalog/    ← 노드 카탈로그 (RAG 코퍼스 + 검색 인덱스)
│   ├── models/     ← Pydantic 스키마 (compose req/res)
│   └── main.py     ← FastAPI 앱 (API_Server 에 노출되는 HTTP API)
├── scripts/        ← 모델 다운로드, llama-server 기동 헬퍼
├── config/         ← .env.example, llama-server 설정
├── docs/           ← 설계 문서 (SPLIT.md 등)
├── plans/          ← PLAN_NN_*.md
└── tests/          ← pytest
```

**`AI_Agent/` 루트에 `.py` 파일 직접 생성 금지.**

## 기술 스택 (예정)

```python
from fastapi import FastAPI
from pydantic import BaseModel
import httpx       # llama-server OpenAI-호환 API 호출
# 후보:
# - openai SDK (llama-server 호환)
# - sentence_transformers (BGE-M3 fallback 임베딩)
# - transformers + torch (Gemma E2B pooling)
```

```
# 런타임 의존
- llama.cpp (`llama-server` 바이너리, Dockerfile 에서 빌드)
- unsloth/gemma-4-26B-A4B-it-GGUF (UD-Q4_K_M) — 모델 가중치 (HF). unsloth 는 plain Q4_K_M 미발행, UD-* (Unsloth Dynamic) 시리즈만 제공.
```

## 핵심 엔드포인트 (API_Server 가 호출)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/v1/compose` | 자연어 → WorkflowSchema JSON (SSE 또는 non-stream) |
| POST | `/v1/embed` | 텍스트 → 벡터 (노드 검색용) |
| GET  | `/v1/health` | llama-server + 모델 ready 여부 |

## 실행 모드

- **로컬 개발**: `uvicorn app.main:app --port 8100` (llama-server 는 별도 터미널)
- **컨테이너 (로컬 GPU)**: `Dockerfile` ENTRYPOINT `entrypoint.sh` 가 llama-server + uvicorn 동시 기동
- **Modal (production)**: `modal deploy AI_Agent/scripts/modal_app.py` — Modal 이 Dockerfile 빌드 + Volume 마운트 + `@enter()` 부팅. 모델 다운로드는 1회: `modal run AI_Agent/scripts/modal_app.py::download_model`

## 인터페이스

- **업스트림**: `API_Server` — `/api/v1/ai/compose` 가 본 모듈의 `/v1/compose` 를 프록시
- **다운스트림**:
  - llama.cpp `llama-server` (컨테이너 내 서브프로세스, localhost:8080)
  - (선택) Anthropic API — dev/fallback 백엔드
  - `Database` — 직접 호출 없음. 사용량 메터링은 API_Server 레이어에서 수행

## 보안 주의사항

- `/v1/*` 엔드포인트는 **Bearer 토큰 검증** (env `AGENT_BEARER_TOKEN`, Modal Secret `agent-bearer-token`).
  Modal 은 endpoint 를 public HTTPS 로 노출하므로 토큰이 단일 게이트. 토큰 값은 GCP Secret Manager
  `agent-bearer-token-staging` 와 동기화 (API_Server 가 GCP 에서 읽고 Bearer 헤더 부착).
- `/v1/health` 만 토큰 없이 접근 가능 (Modal 콜드스타트 readiness probe + 외부 모니터링용).
- 모델 가중치는 공개 GGUF (unsloth/gemma-4-26B-A4B-it-GGUF). `HF_TOKEN` Modal Secret 은
  rate-limit 회피용 (없어도 다운로드 가능, 비권장).
- 프롬프트에 사용자 자격증명·개인정보 포함 금지.
  API_Server 가 정화한 컨텍스트만 수신한다.

## 관련 PLAN / 메모리

- 주 PLAN: `plans/PLAN_11_HACKATHON_SUBMISSION.md` (작성 예정)
- **분할 스펙**: [`docs/SPLIT.md`](./docs/SPLIT.md) — API_Server 와의 경계·이동 매핑·HTTP 계약 초안
- 모델·서빙 결정 근거: auto-memory `project_gemma4_model_decisions.md`
- 해커톤 배경: `project_gemma4_hackathon.md`
- 백엔드 swap 계약: `project_llm_backend_swap_plan.md`
