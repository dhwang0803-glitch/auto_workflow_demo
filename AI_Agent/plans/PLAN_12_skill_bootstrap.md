# PLAN_12 — Skill Bootstrap + 런타임 하네스 통합 파이프라인

> **Status**: Draft (2026-04-25) · **Owner**: dhwang0803 · **ADR**: [ADR-022](../../docs/context/decisions.md#adr-022) · **선행 PLAN**: PLAN_11 (종결) · **마감**: 2026-05-18 (해커톤)

---

## 1. 목표

PLAN_11 종결 후 차별화 재검토 결과 (ADR-022) 채택된 방향을 W2-W3 안에 구현. 단일 목표:

> **사용자가 자기 팀의 정책을 한 번 선언하면, 이후 모든 워크플로우 생성/수정에 그 정책이 자동 적용/검증되는 파이프라인을 만든다.**

정책 선언 채널은 두 가지 (같은 backend 통합):
1. **문서 업로드** (Persona B — 5인 팀, 핸드북 PDF 보유)
2. **대화형 인터뷰** (Persona A — 1인 사업자, 문서 없음)

## 2. 페인포인트 매핑

현업 워크플로우 자동화 미사용 이유 3대 (ADR-022 Context):

| # | 페인 | 본 PLAN 의 풀이 |
|---|---|---|
| 1 | 실제 워크플로우와 다름 | 문서 업로드 시 SOP 가 starter template 으로 흡수 → 부분 완화 |
| 2 | 매번 팀 정책에 맞춰 수정 | **직격** — 정책을 Skill 로 코드화 후 모든 compose 호출에 자동 주입 |
| 3 | AI 결과 신뢰 불가 | **직격** — 정책 인용 (출처 추적) + 사람 검토 단계 + adversarial harness (W4) |

## 3. 범위

### In Scope (W2-W3, ~10일)

- 통합 파이프라인 backend (정책 추출 / 갭 분석 / 답변→skill / 도메인 분류)
- DB 스키마 5개 (`skills`, `skill_sources`, `skill_applications`, `policy_documents`, `policy_extractions`)
- 문서 업로드 + 파싱 (PDF / MD / plain text)
- 임베딩 인덱싱 (BGE-M3, 청크 단위)
- Skill retrieval (compose 시 top-K query→skill)
- 검토/편집 UI (skill 카드)
- 인터뷰 UI (ChatPanel 재사용)
- Compose 시 skill 컨텍스트 주입 + 인용 표시

### Out of Scope (보류 / future)

- **Adversarial harness 자동화** — W4 별도 작업 (시드 룰만 본 PLAN 후반에 준비)
- **Notion / Google Drive 통합** — Drive OAuth 인프라는 있으나 본 PLAN 에서는 plain 업로드만
- **다중 워크스페이스 멤버십** — 워크스페이스 = 팀 단순 모델
- **MCP server 노출** (외부 팀 skill 공유) — future
- **자동 skill aging / 폐기** — 명시 삭제만 지원
- **자동 충돌 감지** — MVP 는 사용자 검토 단계에서 사람이 발견

## 4. 통합 파이프라인 단계별 명세

```
[사용자 입력 0개+ 문서 + 자유 답변] 
  ↓
(1) 문서 파싱 (PDF/MD/text → 청크)
  ↓
(2) 청크 임베딩 (BGE-M3 → policy_extractions.embedding)
  ↓
(3) 정책 추출 LLM (청크 → 후보 skill JSON list)
  ↓
(4) 갭 분석 LLM (도메인 표준 정책 vs 추출된 정책 → 부족분)
  ↓
(5) 타겟팅 질문 생성 (갭 → 자연어 질문 5-10개, 도메인 분류 결과 활용)
  ↓
(6) 대화형 인터뷰 (사용자 답변 수집)
  ↓
(7) 답변→Skill 변환 LLM (질문+답변 컨텍스트 → 구조화 skill JSON)
  ↓
(8) 사람 검토 UI (모든 skill 카드 표시, 편집/거절/승인)
  ↓
(9) 활성 skill 저장 (workspace scope)
```

각 단계 분기:
- **docs 풍부한 팀**: (1)-(4) 가 핵심, (5)-(7) 은 갭 적어 1-2 질문
- **docs 없는 1인**: (1)-(4) skip, (5) 는 도메인 표준 정책 풀세트 → 5-10 질문
- 같은 storage (`skills`, `skill_sources`), 같은 검토 UI, 같은 적용 메커니즘

## 5. DB 스키마 (Database 브랜드)

```sql
CREATE TABLE skills (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    name VARCHAR(255) NOT NULL,
    description TEXT,                          -- 자연어 설명 (사용자가 본문)
    condition JSONB NOT NULL,                  -- 적용 조건 (node type, field 패턴, value 매칭)
    action JSONB NOT NULL,                     -- 적용 액션 (default 주입, 검증 룰, 가드 추가)
    scope VARCHAR(50) NOT NULL,                -- 'workspace' (MVP) | 'user' | 'team' (future)
    status VARCHAR(20) NOT NULL DEFAULT 'active',  -- 'active' | 'pending_review' | 'rejected' | 'archived'
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_skills_workspace_active ON skills(workspace_id, status) WHERE status = 'active';

CREATE TABLE skill_sources (
    id UUID PRIMARY KEY,
    skill_id UUID NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    source_type VARCHAR(20) NOT NULL,          -- 'document' | 'conversation' | 'observation'
    source_ref JSONB NOT NULL,                 -- 문서면 {document_id, chunk_index}, 대화면 {session_id, turn_index}
    extracted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE skill_applications (
    id UUID PRIMARY KEY,
    skill_id UUID NOT NULL REFERENCES skills(id),
    workflow_id UUID,                          -- 적용된 워크플로우 (nullable: compose 단계만 거치고 저장 안 한 경우)
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    citation TEXT NOT NULL                     -- 사용자 친화적 인용 (UI 에 노출)
);

CREATE TABLE policy_documents (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    filename VARCHAR(512) NOT NULL,
    content_hash VARCHAR(64) NOT NULL,         -- SHA256 (재업로드 시 중복 검출)
    mime_type VARCHAR(100) NOT NULL,
    raw_content BYTEA,                          -- 또는 GCS URI (대용량 시)
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, content_hash)
);

CREATE TABLE policy_extractions (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES policy_documents(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding VECTOR(1024),                    -- BGE-M3 dim. pgvector 확장 필요
    extracted_skill_id UUID REFERENCES skills(id),  -- 이 청크에서 추출된 skill (nullable)
    UNIQUE (document_id, chunk_index)
);
CREATE INDEX idx_policy_extractions_embedding ON policy_extractions USING ivfflat (embedding vector_cosine_ops);
```

**고려 사항**:
- `pgvector` 확장 필요 (Cloud SQL Postgres 16+ 지원 — ADR-018 인프라 호환)
- `raw_content` BYTEA vs GCS URI: PDF 평균 ~500KB. 작은 워크스페이스 가정 시 BYTEA, 스케일 우려되면 GCS (PLAN_12 에선 BYTEA 로 시작)
- embedding 인덱스는 ivfflat 우선 (HNSW 는 pgvector 0.5+, 우리 버전 확인 필요)

## 6. LLM 호출 종류 + Multi-turn 인터랙션 모델

### 호출 종류 (4종)

| 호출 | 입력 | 출력 | 빈도 |
|---|---|---|---|
| **policy_extract** | 청크 텍스트 | skill 후보 JSON list (조건+액션 페어 단위) | 청크당 1회 |
| **domain_classify** | 자유 텍스트 ("어떤 일을 하시나요?") | 카테고리 (e-commerce / 서비스업 / 컨설팅 / 컨텐츠 / NPO / 기타) | 인터뷰 시작 1회 |
| **gap_analyze** | (도메인, 추출된 skill 리스트) | 부족 정책 list + 질문 후보 | 인터뷰 직전 1회 |
| **answer_to_skill** | (질문, 답변, 도메인) | 구조화 skill JSON | 답변당 1회 |

추가로 기존 `compose` 호출은 multi-turn + skill retrieval 컨텍스트 추가됨.

### Multi-turn 인터랙션 모델 (single-shot 폐기)

| 차원 | 값 | 비고 |
|---|---|---|
| 턴당 max_tokens | **1024** | 4096 → 1/4 축소 |
| 턴당 timeout | **90s** | PR #125 의 240s 는 single-shot 가정. 본 PLAN 머지 시 60-90s 로 재패치 (별도 PR) |
| streaming | **필수** | SSE first-token <5s 목표 |
| 상태 저장 | 세션 + skill DB | API_Server DB 가 진실 공급원 |
| skill 주입 | **retrieval-only** (top-K=5) | broadcast 금지 |

### 컨텍스트 budget (compose 호출 기준)

| 구성 | 토큰 | 비고 |
|---|---|---|
| 시스템 프롬프트 + node catalog | ~500 | KV cache 안정 |
| 활성 skill top-K (5개) | ~1000 | BGE-M3 query similarity 기반 |
| 대화 history (5-10턴) | ~1500 | 슬라이딩 윈도우 |
| 사용자 메시지 | ~200 | |
| **입력 합계** | **~3200** | |
| 출력 max | 1024 | |

**latency**:
- 첫 턴 (cold prefill): ~50s (3200 / 150 tok/s prefill + 1024 / 35 tok/s gen)
- 이후 턴 (KV warm, system+skills 안정): ~32s (변동분 ~500 tok prefill + 1024 gen)

**KV cache 전략**: llama.cpp slot 모드. 시스템 프롬프트 + 활성 skill 컨텍스트는 안정 prefix 로 둬서 첫 턴 후부터 prefill 단축.

## 7. 데모 시퀀스 (영상)

ADR-022 Decision §5 의 페르소나 우선순위 따름. **Persona B 첫 60초 메인, Persona A 보조 ~20초**.

| 시간 | 화면 | 페르소나 | narrative |
|---|---|---|---|
| 0:00-0:10 | "정책 문서 있으면 올려주세요 — 없어도 괜찮아요" | (intro) | |
| 0:10-0:35 | Persona B: 핸드북 PDF 업로드 → 7개 정책 추출 → 1개 갭 질문 ("PII 정의는?") → 검토/활성 | B | "AI 가 우리 핸드북을 읽고 정책을 만들었어요" |
| 0:35-0:55 | Persona B: "환불 처리 워크플로우 만들어줘" → draft 에 정책 #3 + #5 자동 적용 + 인용 | B | "처음부터 정책 내장" |
| 0:55-1:15 | Persona A 보조컷: 문서 없이 5질문 인터뷰 (애니메이션 가속) → 5 skill 생성 | A | "문서 없어도 같이 만들 수 있어요" |
| 1:15-1:45 | Persona B 복귀: `from` 필드 3회 수정 → 패턴 토스트 → 명시적 + 관찰 정책 융합 | B | "진화하는 정책 라이브러리" |
| 1:45-2:00 | adversarial harness — 정책 위반 시나리오 자동 검출 + 가드 추가 | (closed loop) | "검증도 같은 정책 기반" |

라이브 시연 vs 시드 재생 결정은 **W4 실측 후** (ADR-022 미해결, 본 PLAN 종결 후 결정).

## 8. 미해결 결정 → 본 PLAN 에서 확정

ADR-022 Update §1-5 의 미해결 항목을 본 PLAN 에서 확정:

### 8.1 추출 정책 단위
**결정**: **한 조건+액션 페어 = 1 skill**.
- 너무 잘게 (단어 단위) 쪼개면 skill 폭증 + 적용 정확도 ↓
- 너무 크게 (문단 단위) 묶으면 부분 적용/거절 불가
- "if X then Y" 의 액셔너블 단위로 추출. 예: "외부 도메인으로 발송 시 → 팀장 승인 필요" = 1 skill.

### 8.2 모호한 정책 처리
**결정**: 추출 시 **"구체화 필요" 플래그** 부여 → 후속 질문으로 보완.
- "PII 조심하세요" 같은 추상 정책은 actionable 한 형태가 아님
- LLM 추출 프롬프트에 "조건과 액션이 모호하면 needs_clarification=true 표시" 강제
- 인터뷰 단계에서 자동으로 follow-up 질문 ("PII 의 정의는?", "조심한다는 게 어떤 액션인가요?") 생성 → 구체화

### 8.3 정책 충돌 감지
**결정**: **MVP 는 사람 검토에 위임**. 자동 검출은 W4 이후 (또는 future PLAN).
- 검토 UI 에 같은 도메인/필드 영향 skill 들 그룹화 표시 → 사람이 한눈에 충돌 파악
- "이 두 정책이 같은 필드를 지정합니다 — 우선순위?" 알림은 W3 후반에 시도 (자동 검출 룰 단순)

### 8.4 버전 관리
**결정**: 재업로드 시 **diff 표시 + 항목별 적용/유지/삭제 선택**. 자동 머지 X.
- `policy_documents.content_hash` 로 동일 내용 재업로드 차단
- 다른 hash 면 → 추출 재실행 → 기존 활성 skill 과 비교 → 사용자가 항목별 결정
- 자동 머지/자동 폐기는 신뢰 깨뜨림

### 8.5 팀 경계 모델
**결정**: **워크스페이스 = 팀**. 단순 모델로 시작.
- 한 사용자는 여러 워크스페이스 멤버 (기존 user-workspace 다대다)
- skill 은 `workspace_id` scope. 다른 워크스페이스에는 자동 노출 X
- 다중 멤버십 / 팀간 skill 공유 / MCP 노출은 future

## 9. W2-W3 작업 분해 (10일)

### W2 후반 (04/26-05/04, 9일 가용)

| # | 작업 | 브랜드 | 일수 |
|---|---|---|---|
| W2-1 | DB 스키마 마이그레이션 (5 테이블 + pgvector 활성화) | Database | 0.5d |
| W2-2 | 도메인 분류 LLM (자유텍스트→카테고리, 또는 칩 UI 선택) | AI_Agent | 0.5d |
| W2-3 | 도메인별 표준 정책 정의 (e-commerce / 서비스업 / 컨설팅 / 컨텐츠 / NPO 5개 도메인 × 5-10 정책 시드) | AI_Agent (정적 데이터) | 1d |
| W2-4 | gap_analyze LLM 프롬프트 + 답변→skill LLM 프롬프트 | AI_Agent | 1d |
| W2-5 | 인터뷰 UI (ChatPanel 재사용 + 도메인 칩 + 진행도) | Frontend | 1d |
| W2-6 | skill 카드 컴포넌트 + 검토 UI (편집/거절/승인) | Frontend | 1d |
| W2-7 | API 엔드포인트: `POST /api/v1/skills/bootstrap` (인터뷰 시작) + `POST /api/v1/skills/answer` (턴) + `POST /api/v1/skills/{id}/approve` | API_Server | 1d |
| W2-8 | E2E 검증 (Persona A 풀세트, 문서 없이 5질문 → 5 skill 생성 → 활성) | 통합 | 1d |
| **W2 합계** | | | **7d** (~9d 가용 안에 fit, 2d 버퍼) |

### W3 (05/05-05/12, 7일 가용)

| # | 작업 | 브랜드 | 일수 |
|---|---|---|---|
| W3-1 | 문서 업로드 UI + 파일 핸들링 | Frontend | 1d |
| W3-2 | PDF/MD 파서 + 청킹 (pdfminer.six 또는 pypdf) | AI_Agent | 0.5d |
| W3-3 | BGE-M3 임베딩 인덱싱 + DB 저장 | AI_Agent | 0.5d |
| W3-4 | policy_extract LLM 프롬프트 (구조화 출력) | AI_Agent | 1d |
| W3-5 | 두 path 통합 — 갭 분석이 docs 추출 결과 + 도메인 표준 비교 | AI_Agent | 1d |
| W3-6 | Compose 시 skill retrieval (top-K) + 컨텍스트 주입 + 인용 | API_Server | 1d |
| W3-7 | E2E 검증 (Persona B 풀세트: PDF 업로드 → 7 skill + 1 갭 질문 → 활성 → compose 시 적용 + 인용) | 통합 | 1d |
| W3-8 | adversarial harness **시드 룰** 준비 (자동화 X, 영상용 정책 위반 시나리오 3건 사전 작성) | AI_Agent | 0.5d |
| **W3 합계** | | | **7d** (~7d 가용 안에 fit, 0d 버퍼) |

### W4 (05/13-05/18, 6일)

영상 + writeup + adversarial harness 자동화 (시간 남으면). 본 PLAN 범위 외.

## 10. PR 분할 계획

작은 단위 PR + 브랜치 경계 준수 (`feedback_branch_boundaries.md`).

| # | 브랜드 | 내용 | 종속 |
|---|---|---|---|
| **#127** | Database | DB 스키마 + 마이그레이션 (W2-1) + pgvector 활성화 | — |
| **#128** | AI_Agent | 도메인 분류 + 표준 정책 시드 + gap_analyze + answer_to_skill (W2-2/3/4) | — |
| **#129** | API_Server | skill bootstrap 엔드포인트 (W2-7) | #127 |
| **#130** | Frontend | 인터뷰 UI + 도메인 칩 (W2-5) | #129 |
| **#131** | Frontend | skill 카드 + 검토 UI (W2-6) | #129 |
| **#132** | API_Server | timeout 60s + max_tokens 1024 재조정 (multi-turn 전환 패치) | — (병렬) |
| **#133** | AI_Agent | 문서 파서 + BGE-M3 인덱싱 + policy_extract (W3-2/3/4) | #127 |
| **#134** | Frontend | 문서 업로드 UI (W3-1) | #133 |
| **#135** | AI_Agent | gap analysis 통합 (W3-5) | #133 |
| **#136** | API_Server | compose retrieval+inject (W3-6) | #127, #133 |

총 10 PR. PR #132 는 PR #125 의 single-shot timeout 패치를 multi-turn 으로 재조정하는 small PR (별도 분리 — 다른 PR 에 묶이면 회귀 어려움).

## 11. 리스크 + 완화

| 리스크 | 영향 | 완화 |
|---|---|---|
| pgvector 확장이 Cloud SQL 인스턴스에 미설치 | 임베딩 인덱싱 blocker | 마이그레이션 첫 단계에서 `CREATE EXTENSION` 시도 + 실패 시 인스턴스 재생성 |
| BGE-M3 임베딩 비용/지연 (Modal 또는 별도 서비스) | 인덱싱 시간 ↑ | MVP: AI_Agent Modal 에 BGE-M3 같이 로드 (L4 GPU 여유, GGUF 16.9GB 외 BGE-M3 ~2GB 추가). 별도 서비스는 future |
| 도메인 표준 정책 시드 부재 | gap analysis 부정확 | 5개 도메인 × 5-10 정책 하드코딩 (W2-3). 추후 LLM 생성으로 대체 |
| Multi-turn KV cache 가 llama.cpp slot 에서 안정적 작동 안 함 | latency 목표 미달 | W2 첫 작업으로 KV cache 동작 검증 (간단 multi-turn 테스트). 안 되면 prompt cache 만으로 fallback |
| Persona A 인터뷰 가속 영상이 자연스럽지 않음 | 영상 narrative 약화 | W4 실측 후 라이브 vs 시드 재생 결정 (ADR-022 미해결 항목) |
| W3 일수 부족 (0d 버퍼) | 일정 미스 | W2-8 의 E2E 검증을 W3-1 과 병행 가능 → 1d 추가 확보 |
| AI Composer 기존 코드 multi-turn 미고려 (single-shot 가정) | 리팩터 부담 | PR #132 + PR #136 가 인터페이스 변경. 기존 PLAN_02 stub backend 인터페이스 유지로 backward compat 유지 |

## 12. 후속 영향 (PLAN_13 / future)

본 PLAN 이 닫지 않은 후속:

- **PLAN_13 (가능)**: Adversarial harness 자동화 — 정책 → 자동 위반 시나리오 생성 + 가드 자동 추가. 본 PLAN W3-8 의 시드 룰을 자동화한 형태.
- **MCP server 노출**: 워크스페이스 skill 묶음을 외부 팀이 import 할 수 있는 MCP 형태로 export.
- **자동 충돌 감지**: 활성 skill 간 모순 자동 검출 (룰 기반 시작, LLM 기반 확장).
- **Skill aging**: 일정 기간 미사용 skill 자동 archived 제안.
- **Trace-driven refinement**: 실행 trace → 패턴 → skill 후보 (관찰 기반 보강).
- **다중 워크스페이스 / 다중 멤버십**: 사용자가 여러 팀에 동시 소속 시 skill 우선순위 / 머지 정책.
- **Notion / Drive 통합**: 기존 Drive OAuth 인프라 (PLAN_06 OAuth) 재사용해 SOP 자동 동기화.

## 13. 관련 ADR / 메모리 / 문서

- **ADR-022** (`docs/context/decisions.md`) — 본 PLAN 의 결정 기록 (런타임 하네스 + Skill Bootstrap)
- **ADR-018** — Cloud SQL Postgres + Secret Manager (DB 스키마 호환 기반)
- **ADR-019** — OAuth (Drive 통합 미래 작업의 기반)
- `docs/harness_engineering_guide.md` — 기존 dev-time 하네스 (본 PLAN 의 컨셉 부모)
- 메모리 `project_skill_bootstrap_design.md` — 설계 결정 요약
- 메모리 `project_gemma4_hackathon.md` — 트랙 / 예산 / 영상 컨테이너
- 메모리 `project_llm_backend_swap_plan.md` — multi-turn 전환에 따른 PR #125 timeout 재조정 주의
- 메모리 `feedback_branch_boundaries.md` — 본 PLAN PR 10건 진행 시 준수
- 메모리 `feedback_test_before_pr.md` — 외부 검증 (마이그레이션 / E2E) 은 PR 오픈 전 feature 브랜치에서 완료
- PR #125 (머지됨) — single-shot 가정 timeout 패치. PR #132 가 multi-turn 으로 재조정
- PLAN_11 (종결) — Modal Gemma 4 호스팅 (본 PLAN 의 전제 인프라)
