# Project MAP — 디렉토리/파일 역할 맵

> "이 파일은 뭐하는 애야?"에 답하는 단일 출처. 새 최상위 폴더/파일 추가 시 함께 갱신.

## 최상위 구조 (main 브랜치)

```
auto_workflow_demo/
├── _claude_templates/   ← 브랜치별 CLAUDE.md 템플릿 (post-checkout 훅이 복사)
├── _agent_templates/    ← 에이전트 지시 문서 (TDD/보안/리팩터 역할별)
├── .claude/commands/    ← 슬래시 커맨드 정의 (예: /PR-report)
├── .githooks/           ← post-checkout 훅 (브랜치 전환 시 폴더 자동 스캐폴딩)
├── .github/             ← PR 템플릿 등
├── docs/context/        ← 본 문서군. 아키텍처/결정/맵
└── README.md
```

`main`에는 **공통 설정만** 있고, 실제 소스는 각 브랜치(`API_Server` / `Database` / `Execution_Engine` / `Frontend`)에 격리된다.

`docs` 브랜치는 **위키 전용**: `docs/context/*` 편집만 허용되며, 코드 브랜치는 이 위키를 읽기 전용으로 참조한다. 자세한 규칙은 [`_claude_templates/CLAUDE_docs.md`](../../_claude_templates/CLAUDE_docs.md) 참고.

## 브랜치별 구조

### `API_Server` (Core Layer — FastAPI)
```
API_Server/
├── app/
│   ├── routers/         workflows.py / executions.py / agents.py / webhooks.py
│   ├── services/        workflow_service / dag_scheduler / trigger_manager / agent_manager
│   ├── models/          Pydantic 스키마 (WorkflowSchema, NodeConfig …)
│   └── main.py          FastAPI 앱 + DI 조립
├── tests/               pytest + httpx TestClient
├── config/              환경별 yaml
└── agents/              _agent_templates 복사본
```
세부: [`_claude_templates/CLAUDE_API_Server.md`](../../_claude_templates/CLAUDE_API_Server.md)

### `Database` (Data Layer — PostgreSQL)
```
Database/
├── schemas/             CREATE TABLE/INDEX DDL
├── migrations/          YYYYMMDD_*.sql 이력
├── src/
│   ├── repositories/    Postgres{Workflow,Execution}Repository + CredentialStore
│   └── models/          SQLAlchemy ORM
├── scripts/             migrate.py / seed.py / validate.py
├── tests/               pytest + 실제 테스트 DB
└── docs/                ERD, 설계
```
세부: [`_claude_templates/CLAUDE_Database.md`](../../_claude_templates/CLAUDE_Database.md)

### `Execution_Engine` (Execution Layer — Celery + Agent)
```
Execution_Engine/
├── src/
│   ├── nodes/           BaseNode + HTTP/Condition/Code + NodeRegistry
│   ├── dispatcher/      serverless.py (Celery) / agent_client.py (WS)
│   ├── runtime/         executor.py (DAG) / sandbox.py (RestrictedPython+Docker)
│   └── agent/           main / heartbeat / command_handler (고객 VPC 데몬)
├── scripts/             worker.py / agent_run.py
├── tests/               pytest (노드 단위 + 통합)
├── config/              Celery 설정 등
└── docs/                노드 가이드, 샌드박스 설계
```
세부: [`_claude_templates/CLAUDE_Execution_Engine.md`](../../_claude_templates/CLAUDE_Execution_Engine.md)

### `Inference_Service` *(신설 예정 — ADR-008)*
```
Inference_Service/
├── serving/             vLLM 엔트리포인트, OpenAI 호환 API 래퍼
├── models/              Gemma 4 가중치 관리 (다운로드 스크립트, 체크섬)
├── config/              vLLM 실행 옵션, 양자화, 토크나이저 프리셋
├── scripts/             start_vllm.sh, warmup.py, canary_check.py
└── tests/               서빙 헬스체크, structured output 검증
```
세부 템플릿(`_claude_templates/CLAUDE_Inference_Service.md`)과 post-checkout 훅 case 분기는 **후속 작업**. 현재는 ADR-008 초안 기반 예상 구조.

### `Frontend` (Frontend Layer — Next.js)
```
Frontend/
├── src/
│   ├── components/      WorkflowCanvas / NodePalette / NodeConfigPanel / ExecutionMonitor …
│   ├── pages/           editor/[id].tsx, executions/index.tsx
│   └── services/        workflowApi.ts / executionApi.ts / useExecutionStream.ts
├── public/
└── tests/               Jest + Playwright
```
세부: [`_claude_templates/CLAUDE_Frontend.md`](../../_claude_templates/CLAUDE_Frontend.md)

## 핵심 파일 인덱스

| 파일 | 역할 |
|------|------|
| `.githooks/post-checkout` | 브랜치 전환 시 해당 브랜치 폴더 구조를 자동 생성하고 CLAUDE.md를 복사 |
| `.claude/commands/PR-report.md` | `/PR-report` 슬래시 커맨드: 보안 스캔 → 브랜치 폴더만 스테이징 → PR 생성 |
| `_claude_templates/CLAUDE_DEFAULT.md` | 루트 공통 가이드라인 (보안 규칙 등) |
| `_agent_templates/DEVELOPER.md` | TDD Green 단계 구현 에이전트 |
| `_agent_templates/TEST_WRITER.md` | TDD Red 단계 테스트 작성 에이전트 |
| `_agent_templates/SECURITY_AUDITOR.md` | S01-S08 보안 점검 |
| `_agent_templates/IMPACT_ASSESSOR.md` | 4-layer 영향도 분석 |

## 관련 문서

- 전체 아키텍처: [`architecture.md`](./architecture.md)
- 설계 결정 배경: [`decisions.md`](./decisions.md)
