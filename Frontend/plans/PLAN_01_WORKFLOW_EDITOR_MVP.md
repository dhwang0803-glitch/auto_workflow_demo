# PLAN_01 — Workflow Editor MVP (Frontend)

> **브랜치**: `Frontend` · **작성일**: 2026-04-21 · **상태**: Draft
>
> Frontend 브랜드의 최초 착수 PLAN. React Flow 기반 워크플로 에디터를
> 구축하여 사용자가 직접 노드를 팔레트에서 끌어다 캔버스에 배치하고,
> 엣지로 연결하여 실행까지 수행할 수 있게 한다. AI Composer 레이어
> (PLAN_02) 는 본 에디터 위에 주입되므로, 본 PLAN 의 캔버스·속성
> 편집·저장 경로가 안정화되어야 그 위에 쌓을 수 있다.

## 1. 목표

1. Next.js 14 (App Router) + TypeScript 기반 Frontend 프로젝트 스캐폴딩
2. 노드 팔레트 (좌측 사이드바) — `Execution_Engine/src/nodes/` 카탈로그 전체 브라우징
3. 캔버스 (React Flow) — 드래그앤드롭 배치 · 엣지 연결 · 순환 방지 시각 피드백
4. 속성 패널 (우측) — 선택 노드의 config 편집 (노드 별 schema 기반 동적 폼)
5. 워크플로 저장/불러오기 — `POST/GET/PUT /api/v1/workflows` 연동
6. 수동 실행 — `POST /api/v1/workflows/{id}/execute` + 결과 폴링 표시
7. 단일 유저 토큰 개발 모드 — `.env.local` 의 API 토큰을 그대로 사용

## 2. 범위

**In**
- `Frontend/` 하위 Next.js 14 프로젝트 (App Router, `src/` 레이아웃)
- React Flow + Zustand + TanStack Query + Tailwind + shadcn/ui
- 페이지: `/` (워크플로 목록) / `/workflows/[id]` (에디터)
- 컴포넌트:
  - `NodePalette` — 노드 카탈로그 리스트 (카테고리 그룹)
  - `WorkflowCanvas` — React Flow 래퍼 + 커스텀 Node/Edge 컴포넌트
  - `PropertyPanel` — 선택 노드 config 폼 (JSON Schema → React 폼 매핑)
  - `Toolbar` — Save / Execute / Undo-Redo (Undo-Redo 는 Out 으로 밀 수도)
  - `ExecutionResultDrawer` — 실행 상태/결과 폴링 표시
- API 클라이언트 (`src/lib/api.ts`) — 워크플로 CRUD + 실행 래퍼
- 노드 카탈로그 동기화 — 초기에는 백엔드 `GET /api/v1/nodes/catalog` 엔드포인트 신설 (없으면 Execution_Engine registry 기반으로 새 라우터 추가)
- E2E 스모크 테스트 (Playwright 1개 시나리오: 2-노드 워크플로 생성 → 실행 → 결과 확인)

**Out (후속 PLAN)**
- 자연어 → DAG 자동 생성 (PLAN_02 AI Composer)
- 인증/로그인 UI · 회원가입 · 토큰 갱신 — PLAN_03 Auth UI
- OAuth consent flow UI (Google Workspace 등) — PLAN_04 OAuth UI
- 스케줄러/웹훅 트리거 설정 UI — PLAN_05 Trigger UI
- 실행 로그 스트리밍 뷰어 — PLAN_06 Log Viewer
- 자격증명 (Credentials) CRUD UI — PLAN_07 Credentials UI
- 워크플로 버전/스냅샷, 공유/템플릿 마켓 — 향후

## 3. 노드 카탈로그 동기화 전략

프론트엔드는 `Execution_Engine/src/nodes/` 의 노드 목록과 각 노드의 config
schema 를 알아야 한다. **두 가지 선택**:

| 옵션 | 내용 | 트레이드오프 |
|------|------|-------------|
| A. 런타임 조회 | `GET /api/v1/nodes/catalog` 엔드포인트 신설 (API_Server) → registry 역직렬화 | 백엔드 1 엔드포인트 추가 필요. 노드 추가 시 프론트 변경 불필요 |
| B. 빌드타임 주입 | 백엔드 `registry` 를 JSON 으로 덤프해서 Frontend 빌드 시 포함 | 노드 추가 시 Frontend 재빌드 필요. 대신 런타임 의존성 없음 |

**채택: A** — 노드가 현재 빠르게 확장 중이므로 런타임 조회가 유연함.
각 노드는 `{name, category, display_name, description, config_schema}` 를
반환한다. `config_schema` 는 JSON Schema 로, Frontend 의 `PropertyPanel`
이 이것을 소비해 동적 폼을 렌더한다.

**선결 과제**: API_Server 에 `app/routers/node_catalog.py` 신설.
본 PLAN 에 포함하되 API_Server 측 PR 로 먼저 머지 후 Frontend 연동.

## 4. 기술 스택 및 의존성

```json
{
  "next": "14.x",
  "react": "18.x",
  "typescript": "5.x",
  "reactflow": "11.x",
  "zustand": "4.x",
  "@tanstack/react-query": "5.x",
  "tailwindcss": "3.x",
  "@radix-ui/*": "shadcn 종속성",
  "zod": "3.x",
  "react-hook-form": "7.x"
}
```

- **Node 패키지 매니저**: `pnpm` (디스크 절약, 모노레포 확장 대비)
- **Lint/Format**: ESLint (next/core-web-vitals) + Prettier
- **Dev 포트**: 3000 (백엔드는 8000)
- **CORS**: API_Server 측 이미 허용 설정 확인 필요 (없으면 추가)

## 5. 캔버스 동작 사양

- **드래그앤드롭**: 팔레트에서 캔버스로 drop 시 `addNode(type, position)` Zustand action 호출
- **엣지 연결**: React Flow `onConnect` 훅에서 `source_handle`, `target_handle` 기록
- **유효성 검사**: 저장 전 클라이언트에서 `dag_validator` 동등 로직 수행 (순환/고아 노드). 서버측 검증이 진실 공급원이지만 UX 피드백용
- **자동 레이아웃**: `dagre` 기반 `Auto Layout` 버튼 (AI Composer 가 생성한 DAG 를 보기 좋게 정렬할 때 필수이므로 MVP 에 포함)
- **Undo/Redo**: Zustand middleware (`zundo`) — MVP 에 포함 (에디터 필수 UX)

## 6. 속성 패널 동적 폼

- 노드 선택 시 우측 패널에 `config_schema` 기반 폼 렌더
- JSON Schema → React 컴포넌트 매핑: `@rjsf/core` (React JSON Schema Form) 사용 검토 vs 직접 매퍼 작성
  - **채택: 직접 매퍼** (기본 타입 5종만 지원: string / number / boolean / enum / secret_ref). `@rjsf` 는 스타일 커스터마이즈 비용이 큼
- `secret_ref` 필드는 드롭다운 (사용자의 Credentials 목록) — **단, Credentials UI 는 PLAN_07** 이므로 MVP 는 수동 입력 (credential_id 문자열) 허용

## 7. API 연동

| 동작 | 엔드포인트 | 메소드 |
|------|-----------|--------|
| 노드 카탈로그 | `/api/v1/nodes/catalog` | GET |
| 워크플로 목록 | `/api/v1/workflows` | GET |
| 워크플로 조회 | `/api/v1/workflows/{id}` | GET |
| 신규 생성 | `/api/v1/workflows` | POST |
| 업데이트 | `/api/v1/workflows/{id}` | PUT |
| 실행 | `/api/v1/workflows/{id}/execute` | POST |
| 실행 결과 조회 | `/api/v1/executions/{exec_id}` | GET (폴링) |

## 8. 수용 기준

- [ ] `Frontend/` 하위에 Next.js 프로젝트 스캐폴딩 완료 (`pnpm dev` 로 3000 포트 기동)
- [ ] `GET /api/v1/nodes/catalog` 엔드포인트 동작 (API_Server 측 선행 PR)
- [ ] 팔레트에서 노드를 캔버스로 드래그 가능
- [ ] 엣지 연결 + 순환 시 사용자에게 시각 피드백
- [ ] 속성 패널에서 노드 config 편집 시 store 에 반영
- [ ] Save 버튼으로 `POST /api/v1/workflows` 호출, 응답 id 로 URL 갱신
- [ ] Execute 버튼으로 실행 트리거 + 결과 폴링 성공/실패 표시
- [ ] Playwright 스모크: 2-노드 (`http_request` → `transform`) 워크플로 생성 → 저장 → 실행 → 결과 drawer 에 success 표시
- [ ] CORS 이슈 없이 localhost 기동

## 9. 선결 질문

1. **API_Server 노드 카탈로그 엔드포인트 위치** — `app/routers/node_catalog.py` 신설 OK? → API_Server 브랜치의 별도 PR 로 처리
2. **CORS 정책** — 개발 모드는 `localhost:3000` 허용 필요. prod 는 별도 도메인 확보 후 결정
3. **워크플로 실행 모드** — MVP 는 serverless 만 지원 (agent 모드는 PLAN_06+)
4. **타입 공유** — API_Server Pydantic → Frontend TS 타입 동기화. 현재 안: 수동. 후속에서 `openapi-typescript` 로 자동화 검토

## 10. 후속 영향

- **PLAN_02 AI Composer** — 본 에디터의 Zustand store 에 DAG 주입 API 필요 (`store.loadFromJson(dag)`). PLAN_01 에서 이 훅을 미리 노출해 두면 PLAN_02 구현이 순수 additive 가 됨
- **API_Server 측 노드 카탈로그 라우터** — 본 PLAN 의 선결 작업. API_Server 브랜치에서 별도 PR 로 진행
- **ADR 신설 검토** — Frontend 스택 결정 (Next.js + React Flow) 을 ADR-022 로 기록할지 판단 (PLAN 수용 시점에 결정)
