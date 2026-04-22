# PLAN_11 — Kaggle Gemma 4 Good Hackathon 제출

> **브랜치**: `AI_Agent` 주도 · `API_Server`/`infra`/`Frontend` 동반
> **작성일**: 2026-04-22 · **마감**: 2026-05-18 23:59 UTC (D-26)
> **상태**: Active — 프로젝트 최우선 목표
> **선행 스펙**: [`../docs/SPLIT.md`](../docs/SPLIT.md) — API_Server ↔ AI_Agent 분할 경계

## 1. 목표

auto_workflow_demo 프로젝트를 **Kaggle Gemma 4 Good Hackathon** 제출물로 완성한다.
총 상금 $200K. **이중 상금 타겟**: Main/Impact Track (Digital Equity & Inclusivity) +
Special Tech (llama.cpp). 동시 수상 시 $20K+.

평가 **70% 가 non-code** (Impact 40% / Video 30% / Technical 30%). 영상·스토리가
기술만큼 중요 — 엔지니어링만 파고들지 말 것.

## 2. 확정 결정 (2026-04-22)

| 항목 | 결정 | 근거 |
|---|---|---|
| Track | **Digital Equity & Inclusivity** ($10K) | AI Composer "자연어 → 워크플로우" = 코딩 장벽 제거 서사 직결 |
| 팀 구성 | **단독** | 26일 / 코드베이스 이미 숙지. 팀 온보딩 비용 과함 |
| 임베딩 | **Gemma-4 E2B pooling 기본 + BGE-M3 fallback** | novelty + Technical Depth. W1 내 A/B 후 실패 시 전환 |
| Live demo | **Cloud Run GPU L4 min=0** | Spot=중단시 실격. always-on=예산 초과. 콜드스타트 30-60s 는 UX 서사로 상쇄 |
| Plan 문서 | 본 PLAN_11 신설 | PLAN_10 (운영 가드레일) 은 해커톤 후로 보류 |

## 3. 제출 필수 아티팩트

1. **Kaggle Writeup** ≤ 1,500 단어
2. **YouTube 영상** ≤ 3분 (공개, 로그인 불필요)
3. **Public code repo** (본 repo)
4. **Live demo URL** — 심사 기간 내내 유지, 로그인/페이월 금지

## 4. 평가 기준 정렬

| 평가 | 비중 | 우리 측 증빙 |
|---|---|---|
| Impact & Vision | 40% | "코딩 없이 워크플로우 자동화 = 디지털 형평성" 서사. 비개발자 사용 시나리오 3개 (비영리 관리자 / 1인 사업자 / 교육자) |
| Video Pitch & Storytelling | 30% | 3분 영상: 훅 5s / 문제 30s / 라이브 데모 90s / 기술 30s / 클로징 15s |
| Technical Depth & Execution | 30% | Gemma 4 26B-A4B Q4 GGUF on L4 + llama.cpp (Special Tech 정렬). E2B pooling 개인화. SSE 스트림 compose |

## 5. 마일스톤 (26일 역산)

### W1 (04/22-04/28) — 백엔드 교체 + E2E 1건

**Exit 조건**: 로컬에서 자연어 1건 → AI_Agent compose → API_Server → WorkflowSchema 저장까지 통과.

- **PR 1** (AI_Agent + API_Server) — **코드 마이그레이션**. `docs/SPLIT.md §3` 이동 매핑 + `AIAgentHTTPBackend` 신설. `§5.3` 복사→전환→삭제 순서. 본 PLAN 머지 직후 진행될 선행 PR.
- **PR 2** (AI_Agent) — `LlamaCppGemmaBackend` 구현 + Dockerfile 골격 + `llama-server` 서브프로세스 기동 스크립트 (`scripts/run_llama_server.sh`).
- **PR 3** (AI_Agent) — `EmbeddingBackend` Protocol + `GemmaE2BPoolingBackend` + `BgeM3Backend` (fallback). 환경변수 `EMBEDDING_BACKEND` 로 런타임 토글.
- **PR 4** (AI_Agent) — E2B vs BGE-M3 A/B 테스트 (쿼리 5-10개, 노드 검색 Recall@K 비교) + 기본 백엔드 스위치 결정 기록 (`docs/EMBEDDING_CHOICE.md`).

**병렬**: GCP L4 쿼터 신청 (us-central1, 승인 1-3일). 대기 중 로컬 smoke 진행.

### W2 (04/29-05/05) — 배포 + 시나리오 3건

**Exit 조건**: 퍼블릭 demo URL 에서 3개 시나리오 compose→execute 성공.

- **PR 5** (infra) — AI_Agent Cloud Run GPU L4 min=0 배포. 모델 가중치는 GCS bucket 또는 Artifact Registry OCI artifact. Startup probe = `/v1/health`. IAM invoker 로 API_Server 서비스 계정만 접근 허용.
- **PR 6** (AI_Agent) — 노드 카탈로그 RAG. `app/catalog/` 에 임베딩 인덱스 (in-memory FAISS 또는 단순 numpy dot) + 검색 + compose 프롬프트 주입.
- **PR 7** (AI_Agent) — 데모 시나리오 fixture 3개. Digital Equity 서사 정렬:
  - 비영리 관리자: "기부 감사 이메일 자동 발송"
  - 1인 사업자: "고객 문의 Slack 알림 + Notion 로깅"
  - 교육자: "제출 과제 Drive 자동 분류 + 채점 리스트"
- **PR 8** (Frontend) — ChatPanel 콜드스타트 UX. 로딩 스피너 + "Scale-to-zero 아키텍처" 마이크로카피 + "Try one of these" 시나리오 버튼.

### W3 (05/06-05/12) — 폴리싱 + 영상/Writeup 초안

**Exit 조건**: 영상 rough cut + Writeup 초안 작성 완료.

- Frontend 폴리싱: 온보딩 모달, 오류 복구 문구, 실행 결과 미리보기.
- 영상 3분 스크립트 fix → 녹화 → rough cut.
- Writeup 1,500단어 초안 (Impact 40% 서사 / 기술 하이라이트 / llama.cpp Special Tech 포인트).

### W4 (05/13-05/18) — 마감 주간

**Exit 조건**: 05/18 UTC 23:59 이전 제출 완료.

- 영상 편집 → YouTube 업로드 → 공개·로그인 불필요 확인.
- Writeup 완성 (fact-check, 단어 수, 제출 체크리스트).
- Demo URL 최종 안정화: 콜드스타트 시뮬레이션, 에러 가드, 백업 녹화본 Writeup 에 첨부 준비.
- **05/17 최종 드라이런** → **05/18 제출**.

## 6. PR 계획 요약

`docs/SPLIT.md` 의 분할 결정이 PR 1 의 전제. 이후 PR 은 AI_Agent 내부 작업이
대부분이라 API_Server·infra 는 최소 관여.

| # | 주 브랜드 | 주요 변경 | 의존 |
|---|---|---|---|
| 1 | AI_Agent + API_Server | PLAN_02 심볼 이동 + `AIAgentHTTPBackend` | SPLIT spec 머지 후 |
| 2 | AI_Agent | `LlamaCppGemmaBackend` + Dockerfile | PR 1 |
| 3 | AI_Agent | `EmbeddingBackend` (E2B pooling + BGE-M3) | PR 1 |
| 4 | AI_Agent | E2B vs BGE-M3 A/B + 기본 백엔드 결정 | PR 2, PR 3 |
| 5 | infra | Cloud Run GPU 배포 | PR 2 (이미지 준비) |
| 6 | AI_Agent | 노드 카탈로그 RAG | PR 3 |
| 7 | AI_Agent | 데모 시나리오 fixture | PR 4, PR 6 |
| 8 | Frontend | 콜드스타트 UX + 시나리오 버튼 | PR 5 |

## 7. 예산 (해커톤 전체)

| 구간 | 내용 | 금액 |
|---|---|---|
| 개발 26일 | Cloud Run GPU scale-to-zero (tester 1명) | ~$40 |
| Live demo ~3주 | Cloud Run GPU min=0 + DB/Redis | ~$130 |
| 기타 | Artifact Registry / GCS / (선택) 도메인 | ~$10 |
| **총 예상** | — | **~$170** |
| **버퍼 포함 권장** | 쿼터/재측정/재시도 | **~$250** |

## 8. 리스크 + 완화

| 리스크 | 영향 | 완화 |
|---|---|---|
| L4 쿼터 승인 지연 (1-3일) | W2 배포 지연 | W1 첫날 신청. 대기 중 로컬 smoke 진행 |
| Gemma-4 E2B pooling 품질 불량 | Technical Depth 약화 | `EmbeddingBackend` Protocol 로 BGE-M3 fallback 사전 설계 |
| 26B-A4B Q4 GGUF 품질/안정성 (HF 1일 전 업로드) | compose 전반 실패 | fallback: `unsloth/gemma-4-E4B-it-GGUF` (품질↓, VRAM 5GB) |
| 콜드스타트 30-60s | 심사위원 UX 저하 | 로딩 스피너 + "scale-to-zero" 영상 프레이밍. startup probe 후에만 트래픽 수용 |
| 단독 팀 영상 품질 | Video 30% 평가 약화 | 필요 시 편집만 $50-100 외주 옵션 유보 |
| Demo URL 심사 기간 중 다운 | 실격 리스크 | 백업 녹화본 Writeup 에 첨부 |
| HTTP 경계 계약 미확정 | PR 1 이후 블로킹 | `docs/SPLIT.md §4` 에 초안. PR 1 에서 fix |

## 9. 이중 상금 타겟

> *"Projects are eligible to win both a Main Track Prize and a Special Technology Prize."*

- **Main/Impact Track**: Digital Equity & Inclusivity ($10K).
- **Special Tech**: llama.cpp ($10K). 26B-A4B Q4 GGUF on L4 선택이 "resource-constrained hardware" 서사와 자연 정렬.
- 동시 수상 시 **$20K 이상**.

## 10. 관련 PLAN / 메모리 / 문서

- 선행 스펙: [`../docs/SPLIT.md`](../docs/SPLIT.md) — 분할 경계·이동 매핑·HTTP 계약 초안
- 보류: `API_Server/plans/PLAN_10_AI_COMPOSER_OPS.md` — 운영 가드레일 (해커톤 후 재개)
- 확장 기반: `API_Server/plans/PLAN_02_WORKFLOW_CRUD.md` — AI Composer (본 PLAN 이 연장)
- auto-memory `project_gemma4_hackathon.md` — 상금/규칙/평가 상세
- auto-memory `project_gemma4_model_decisions.md` — 모델/서빙 결정 근거 (26B-A4B Q4 GGUF + llama.cpp)
- auto-memory `project_llm_backend_swap_plan.md` — 백엔드 swap 계약 (본 PLAN 의 PR 1-2 에서 실현)
