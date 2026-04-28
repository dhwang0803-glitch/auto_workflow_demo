# Frontend — Claude Code 브랜치 지침

> 루트 `CLAUDE.md` 보안 규칙과 함께 적용된다.

## 관련 문서

- 상류 의존: `API_Server` — REST 콘트랙트 + (예정) WebSocket 실시간 스트림
- 설계 결정 배경: `docs/context/decisions.md`
- PLAN 문서: `Frontend/plans/PLAN_01_*.md`, `PLAN_02_*.md` (+ AI_Agent `PLAN_12` 의 Frontend 작업 항목)

## 모듈 역할

**Workflow Editor + AI 인터페이스 UI** — 사용자가 다음 두 흐름을 수행하는 Next.js 14 (App Router) 웹 클라이언트:

1. **워크플로우 편집** (PLAN_01) — React Flow 캔버스에서 노드를 드래그/연결, 파라미터 편집, 저장, 수동 실행 후 결과 확인
2. **AI Composer 채팅** (PLAN_02) — 자연어 입력 → SSE 스트리밍으로 rationale 토큰 + 제안된 DAG 수신 → Apply 시 캔버스에 반영
3. **Skill Bootstrap Wizard** (PLAN_12 W2-5/W2-6) — 도메인 칩 선택 → 인터뷰 질문 → SkillDraft 생성 → SkillCard 검토 (Approve / Reject / Answer follow-up)

4-레이어 아키텍처 중 **Frontend Layer**. 자체 비즈니스 상태 머신은 Zustand store 에 두고, 서버 캐시는 React Query (TanStack Query) 가 담당한다.

## 파일 위치 규칙 (MANDATORY)

```
Frontend/
├── src/
│   ├── app/                ← Next.js App Router 라우트 (RSC + client component)
│   │   ├── layout.tsx          ← 루트 레이아웃 (QueryProvider 포함)
│   │   ├── page.tsx            ← 홈 (`/`) — workflows 리스트
│   │   ├── workflows/[id]/page.tsx
│   │   └── skills/new/page.tsx ← Skill wizard 진입
│   ├── components/         ← UI 컴포넌트 (직접 실행 X)
│   │   ├── editor/             ← 캔버스/팔레트/속성 패널 (PLAN_01)
│   │   ├── skills/             ← skill-card, skill-wizard (PLAN_12)
│   │   └── workflows-list.tsx  ← 홈 리스트
│   ├── lib/                ← API 클라이언트 + 도메인 유틸 (NOT services/)
│   │   ├── api.ts              ← apiFetch 래퍼 + workflows/executions/nodes
│   │   ├── composer.ts         ← compose JSON + SSE 스트림
│   │   ├── skills.ts           ← bootstrap / answer / approve / reject
│   │   ├── dag.ts              ← DAG 직렬화 유틸
│   │   └── auto-layout.ts      ← dagre 자동 배치
│   ├── store/              ← Zustand (composer-store, editor-store, skill-wizard-store)
│   └── providers/          ← React Query Provider 등 cross-cutting
├── public/                 ← 정적 에셋
├── plans/                  ← `PLAN_NN_*.md`
└── tests/                  ← Playwright (`*.spec.ts`)
```

| 파일 종류 | 저장 위치 |
|-----------|-----------|
| 라우트/페이지 | `src/app/<route>/page.tsx` (App Router — `pages/` 사용 X) |
| 재사용 UI 컴포넌트 | `src/components/<domain>/*.tsx` |
| API 클라이언트 + 도메인 유틸 | `src/lib/*.ts` |
| Zustand store | `src/store/*-store.ts` (한 도메인당 1 파일) |
| Cross-cutting Provider | `src/providers/*.tsx` |
| Playwright 스펙 | `tests/*.spec.ts` |

**`Frontend/` 루트 또는 `src/` 루트에 직접 `.ts`/`.tsx` 파일 생성 금지.**

## 기술 스택

```typescript
// 프레임워크 / 언어
Next.js 14 (App Router) · TypeScript 5 · Tailwind CSS 3

// 핵심 라이브러리
import ReactFlow from "reactflow";           // 워크플로우 캔버스
import { create } from "zustand";            // 클라이언트 상태
import { useQuery } from "@tanstack/react-query"; // 서버 캐시
import dagre from "dagre";                   // 자동 레이아웃
import zundo from "zundo";                   // undo/redo (editor)

// 테스트
import { test, expect } from "@playwright/test";
```

## 핵심 컴포넌트 / 페이지

| 컴포넌트 / 페이지 | 역할 | PLAN |
|------------------|------|------|
| `app/page.tsx` (`WorkflowsList`) | 홈 — workflows 목록 + skill wizard 링크 | PLAN_01 |
| `app/workflows/[id]/page.tsx` | 단일 워크플로우 에디터 | PLAN_01 |
| `app/skills/new/page.tsx` | Skill wizard 진입 | PLAN_12 W2-5 |
| `components/editor/workflow-canvas.tsx` | React Flow 캔버스 (노드/엣지 편집) | PLAN_01 |
| `components/editor/node-palette.tsx` | 노드 카탈로그 드래그 소스 (`/api/v1/nodes/catalog`) | PLAN_01 |
| `components/editor/property-panel.tsx` + `property-form.tsx` | 선택 노드 파라미터 편집 | PLAN_01 |
| `components/editor/result-drawer.tsx` | 실행 결과 (`ExecutionResponse.node_results`) 노드별 표시 | PLAN_01 |
| `components/editor/chat-panel.tsx` | AI Composer SSE 채팅 + Apply draft | PLAN_02 |
| `components/skills/skill-wizard.tsx` | 도메인 칩 → 인터뷰 → 카드 검토 | PLAN_12 W2-5/6 |
| `components/skills/skill-card.tsx` | CONDITION/ACTION/RATIONALE + Approve/Reject/Follow-up | PLAN_12 W2-6 |

## 데이터 흐름

```
워크플로우 편집 (PLAN_01):
  NodePalette (catalog from /api/v1/nodes/catalog)
    → drag onto WorkflowCanvas (React Flow)
    → PropertyPanel 로 NodeConfig 편집
    → editor-store 가 dirty 추적 + zundo undo/redo
    → [Save]   POST /api/v1/workflows  | PUT /api/v1/workflows/{id}
    → [Execute] POST /api/v1/workflows/{id}/execute
    → ResultDrawer 가 ExecutionResponse 폴링 (TERMINAL_STATUSES 도달 시 정지)

AI Composer (PLAN_02):
  ChatPanel → composer-store
    → composeStream (POST /api/v1/ai/compose?stream=true)
    → SSE frames: session / rationale_delta / result / error
    → result.intent ∈ {clarify, draft, refine}
    → draft|refine: pendingDraft 저장 → [Apply] 시 editor-store.applyComposerDraft

Skill Wizard (PLAN_12 W2-5/6):
  DomainPicker → POST /api/v1/skills/bootstrap
    → flat queue of (policy_id, question)
    → AskingTurn ↔ wizard-input → POST /api/v1/skills/answer
    → WizardDraft 누적 → done phase 진입
    → SkillCard 리스트 → POST /skills/{id}/approve|reject
    → needs_clarification → pushFollowUpQuestion → wizard 재진입
```

## 인터페이스

- **업스트림**: `API_Server`
  - REST: `/api/v1/workflows` · `/executions` · `/nodes/catalog` · `/ai/compose` (SSE) · `/skills/*` · `/auth`
  - 인증: `NEXT_PUBLIC_DEV_TOKEN` (로컬 dev) → `Authorization: Bearer <token>` 헤더
  - dev rewrite: `next.config.mjs` 가 `/api/*` → `http://127.0.0.1:8000/api/*`
- **다운스트림**: 사용자 브라우저

## 보안 주의사항

- 자격증명 입력 폼은 **상태에 장기 보존 금지**. 전송 후 즉시 초기화. 비밀번호/토큰을 store/로컬스토리지/세션스토리지에 두지 않는다.
- API 토큰(JWT)은 메모리 또는 `httpOnly` 쿠키. **`localStorage` 사용 금지** (XSS 노출).
- `NEXT_PUBLIC_*` 환경변수는 **클라이언트 번들에 인라인됨** — API 키/시크릿을 절대 넣지 않는다. Dev token 만 가능 (게다가 운영에선 OAuth + 서버 세션으로 교체).
- `.env.local` 은 git 추적 금지 (`.gitignore` 에 포함). `.env.example` 만 커밋.
- AI Composer / Skill Wizard 가 LLM 응답을 그대로 렌더할 때 **HTML/마크다운 raw 삽입 금지** — React 의 기본 escape 만 신뢰하고, `dangerouslySetInnerHTML` 사용 금지.
- 사용자 워크플로우 JSON 에 사용자 자격증명이 포함된 채로 전송되지 않도록 한다 (입력 폼이 즉시 redaction → API 가 CredentialStore 로 저장).

## 검증 명령

```bash
# 타입 체크 + 린트 + 빌드 (PR 오픈 전 필수)
npx tsc --noEmit
npx next lint
npx next build

# Playwright (mock 기반 — API_Server 없이 통과)
npx playwright test tests/ai-composer.spec.ts tests/skill-wizard.spec.ts

# Live 통합 (API_Server uvicorn :8000 필요)
npx playwright test tests/smoke.spec.ts
```

## 관련 PLAN / 메모리

- `Frontend/plans/PLAN_01_WORKFLOW_EDITOR_MVP.md` — React Flow 캔버스 + 실행 트리거 + ResultDrawer
- `Frontend/plans/PLAN_02_AI_COMPOSER.md` — AI Composer 4 PR (non-stream / SSE / Apply)
- `AI_Agent/plans/PLAN_12_skill_bootstrap.md` — Frontend 작업 항목 W2-5 (인터뷰) / W2-6 (검토 카드) / W3-1 (문서 업로드 — 미착수)
- 메모리 `feedback_hackathon_ui_english.md` — UI 텍스트 영어 통일 (Kaggle 심사위원 대응)
- 메모리 `feedback_no_merge_commits_in_branch.md` — 브랜드 브랜치 main 동기화는 `merge` X / `rebase` O
- 메모리 `feedback_test_before_pr.md` — PR 오픈 전 외부 검증 (typecheck/lint/build/playwright) 모두 green
