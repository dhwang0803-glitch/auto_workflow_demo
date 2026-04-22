# AI_Agent — Claude Code 브랜치 지침

> 루트 `CLAUDE.md` 보안 규칙과 함께 적용된다.

## 관련 문서

- 상류 의존: `API_Server` — AI 기능의 공개 엔드포인트·인증·rate limit·SSE 프록시
- 다운스트림: `infra/` — Cloud Run GPU 배포 정의 + 컨테이너 이미지

## 모듈 역할

**AI Orchestration Service** — auto_workflow_demo 의 AI 두뇌.
LLM 추론(Gemma 4 via llama.cpp) + 임베딩(personalized retrieval) + 프롬프트 구성 +
노드 카탈로그 RAG 를 API_Server 로부터 HTTP 경계로 분리해 담는다.

API_Server 는 AI 기능의 **엔드포인트·인증·rate limit·SSE 프록시**만 담당하고,
실제 LLM/임베딩 호출과 프롬프트 오케스트레이션은 전부 이 모듈에서 수행한다.

**배포 단위**: Cloud Run GPU (L4, scale-to-zero). `llama-server` 를 서브프로세스로 임베드해
단일 컨테이너에서 Python FastAPI + llama.cpp 를 함께 기동한다. 콜드스타트 30-60s 는
모델 로드 완료 후 ready probe 통과 — 심사 기간 동안 min=0 유지.

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
- unsloth/gemma-4-26B-A4B-it-GGUF (Q4_K_M) — 모델 가중치 (HF)
```

## 핵심 엔드포인트 (API_Server 가 호출)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/v1/compose` | 자연어 → WorkflowSchema JSON (SSE 또는 non-stream) |
| POST | `/v1/embed` | 텍스트 → 벡터 (노드 검색용) |
| GET  | `/v1/health` | llama-server + 모델 ready 여부 |

## 실행 모드

- **로컬 개발**: `uvicorn app.main:app --port 8100` (llama-server 는 별도 터미널)
- **컨테이너**: Dockerfile 에서 `llama-server` 서브프로세스 + uvicorn 동시 기동
- **Cloud Run GPU**: 단일 컨테이너 배포, min=0 (콜드스타트 수용)

## 인터페이스

- **업스트림**: `API_Server` — `/api/v1/ai/compose` 가 본 모듈의 `/v1/compose` 를 프록시
- **다운스트림**:
  - llama.cpp `llama-server` (컨테이너 내 서브프로세스, localhost:8080)
  - (선택) Anthropic API — dev/fallback 백엔드
  - `Database` — 직접 호출 없음. 사용량 메터링은 API_Server 레이어에서 수행

## 보안 주의사항

- `/v1/*` 엔드포인트는 **내부 서비스 to 서비스 인증**만 허용 (Cloud Run IAM invoker).
  외부 공개 금지 — 모든 공개 접근은 API_Server 를 거친다.
- 모델 가중치는 공개 GGUF 지만 HF Gemma Terms 동의 토큰 필요.
  `scripts/download_model.py` 에서 `HF_TOKEN` env 필수.
- 프롬프트에 사용자 자격증명·개인정보 포함 금지.
  API_Server 가 정화한 컨텍스트만 수신한다.

## 관련 PLAN / 메모리

- 주 PLAN: `plans/PLAN_11_HACKATHON_SUBMISSION.md` (작성 예정)
- **분할 스펙**: [`docs/SPLIT.md`](./docs/SPLIT.md) — API_Server 와의 경계·이동 매핑·HTTP 계약 초안
- 모델·서빙 결정 근거: auto-memory `project_gemma4_model_decisions.md`
- 해커톤 배경: `project_gemma4_hackathon.md`
- 백엔드 swap 계약: `project_llm_backend_swap_plan.md`
