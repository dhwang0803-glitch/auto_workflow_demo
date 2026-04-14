# Frontend — Claude Code 브랜치 지침

> 루트 `CLAUDE.md` 보안 규칙과 함께 적용된다.

## 관련 문서

- 전체 아키텍처 / REST+WebSocket 흐름: [`docs/context/architecture.md`](../docs/context/architecture.md)
- 설계 결정 배경: [`docs/context/decisions.md`](../docs/context/decisions.md)
- 파일 맵: [`docs/context/MAP.md`](../docs/context/MAP.md)
- 하류 의존 (API 소비): [`CLAUDE_API_Server.md`](./CLAUDE_API_Server.md)

## 모듈 역할

**워크플로우 에디터 UI** — 사용자가 캔버스에 노드를 배치하고 선으로 연결하여
자동화 워크플로우를 시각적으로 구성하는 웹 클라이언트.
생성된 워크플로우는 JSON으로 직렬화되어 `API_Server`로 전송된다.

4-레이어 아키텍처 중 **Frontend Layer**를 담당.

## 파일 위치 규칙 (MANDATORY)

```
Frontend/
├── src/
│   ├── components/   ← 재사용 UI 컴포넌트 (직접 실행 X)
│   ├── pages/        ← 페이지 라우트
│   └── services/     ← API_Server 클라이언트 (REST + WebSocket)
├── public/           ← 정적 에셋
└── tests/            ← Jest / Playwright
```

| 파일 종류 | 저장 위치 |
|-----------|-----------|
| 캔버스/노드 컴포넌트 (`WorkflowCanvas`, `NodePalette` 등) | `src/components/` |
| 페이지 (`editor/[id].tsx`, `executions/index.tsx` 등) | `src/pages/` |
| API 클라이언트 (`workflowApi.ts`, `executionApi.ts`) | `src/services/` |
| 실행 상태 실시간 구독 훅 | `src/services/useExecutionStream.ts` |
| Jest 단위 테스트 | `tests/` |

**`Frontend/` 루트 또는 프로젝트 루트에 소스 파일 직접 생성 금지.**

## 기술 스택

```typescript
// 프레임워크
Next.js 14 (App Router) + TypeScript + Tailwind CSS

// 핵심 라이브러리
import ReactFlow from 'reactflow';         // 노드 기반 캔버스
import { useWebSocket } from 'src/services/useExecutionStream';  // 실시간 로그
```

## 핵심 컴포넌트

| 컴포넌트 | 역할 |
|----------|------|
| `WorkflowCanvas` | React Flow 기반 노드/엣지 편집 캔버스 |
| `NodePalette` | 드래그 가능한 노드 목록 (NodeRegistry에서 조회) |
| `NodeConfigPanel` | 선택된 노드의 파라미터 편집 |
| `ExecutionMonitor` | 실행 이력 + 노드별 실시간 상태 |
| `CredentialManager` | 자격증명 등록/관리 UI (평문 저장 금지) |
| `AgentStatus` | 고객 VPC Agent 연결 상태 대시보드 |

## 주요 플로우

```
워크플로우 편집:
  NodePalette → WorkflowCanvas 드래그
    → NodeConfigPanel로 파라미터 설정
    → [저장] POST /api/v1/workflows (JSON 직렬화)
    → [실행] POST /api/v1/workflows/{id}/execute

실행 모니터링:
  WebSocket /api/v1/executions/{id}/stream 구독
    → 노드별 상태(pending/running/success/failed) 실시간 갱신
    → <ExecutionMonitor> 타임라인 표시
```

## 인터페이스

- **업스트림**: `API_Server` — REST API(CRUD, 실행 트리거) + WebSocket(실행 로그 스트림)
- **다운스트림**: 사용자 브라우저

## 보안 주의사항

- 자격증명 입력 폼은 **값을 프론트엔드 상태에 장기 보존 금지**. 전송 후 즉시 초기화.
- API 토큰(JWT)은 `httpOnly` 쿠키 또는 메모리에만 보관. `localStorage` 사용 금지.
