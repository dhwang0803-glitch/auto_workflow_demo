# Architecture — 4-Layer Workflow Automation Engine

> 프로젝트 전체 구조의 단일 진입점. 세부는 각 브랜치 `CLAUDE.md`로 분기.

## 레이어 개요

```
┌─────────────────────────────────────────────────────────┐
│  Frontend Layer  (Next.js + React Flow)                 │
│  - 노드 기반 워크플로우 에디터 UI                         │
│  - WebSocket으로 실행 상태 실시간 구독                    │
└────────────────────────┬────────────────────────────────┘
                         │ REST + WebSocket
┌────────────────────────▼────────────────────────────────┐
│  Core Layer  (FastAPI / API_Server)                     │
│  - 워크플로우 CRUD, DAG 스케줄링, 트리거 감시              │
│  - Execution_Engine 및 Agent 디스패치 조율                │
└──────────┬──────────────────────────────┬───────────────┘
           │ Celery 큐                     │ WebSocket
┌──────────▼──────────────┐   ┌───────────▼───────────────┐
│ Serverless Worker       │   │ Agent (고객 VPC)            │
│ (Cloud Run + Celery)    │   │ - Heavy 유저용 전용 실행기    │
│ - Light/Middle 유저용    │   │ - 외부 반입 불가 데이터 처리  │
└──────────┬──────────────┘   └───────────┬───────────────┘
           │                               │
┌──────────▼───────────────────────────────▼───────────────┐
│  Data Layer  (PostgreSQL 16 + Redis)                     │
│  - Repository 패턴으로 추상화, 직접 SQL 금지               │
│  - 자격증명 AES-256(Fernet) 암호화 저장                   │
└──────────────────────────────────────────────────────────┘
```

## 브랜치 ↔ 레이어 매핑

| 레이어 | 브랜치 | 세부 지침 |
|--------|--------|-----------|
| Frontend | `Frontend` | [`_claude_templates/CLAUDE_Frontend.md`](../../_claude_templates/CLAUDE_Frontend.md) |
| Core | `API_Server` | [`_claude_templates/CLAUDE_API_Server.md`](../../_claude_templates/CLAUDE_API_Server.md) |
| Execution | `Execution_Engine` | [`_claude_templates/CLAUDE_Execution_Engine.md`](../../_claude_templates/CLAUDE_Execution_Engine.md) |
| Data | `Database` | [`_claude_templates/CLAUDE_Database.md`](../../_claude_templates/CLAUDE_Database.md) |

## 실행 모드 (하이브리드 SaaS)

두 가지 실행 경로를 `workflow.settings.execution_mode`로 분기한다:

- **`serverless`** — Light/Middle 유저. Celery → Redis 큐 → Cloud Run 컨테이너.
- **`agent`** — Heavy 유저. 고객 VPC에 설치된 Agent가 WebSocket으로 서버와 상시 연결, 서버가 `execute` 명령을 push.

동일한 `BaseNode` 플러그인 인터페이스와 `NodeRegistry`를 두 모드가 공유한다.

## 주요 데이터 흐름

### 1. 워크플로우 생성/실행 (Serverless)
```
Frontend ──POST /api/v1/workflows──▶ API_Server
                                      │
                                      ├─▶ DAGScheduler (Kahn 위상정렬 + 순환 검사)
                                      ├─▶ WorkflowRepository.save (Database)
                                      └─▶ Celery.enqueue → Worker (Execution_Engine)
                                              │
                                              ├─ CredentialStore.retrieve (실행 시점만)
                                              ├─ Node.execute (병렬: asyncio.gather)
                                              └─ ExecutionRepository.save_result
```

### 2. Agent 모드 실행
```
Agent ──WebSocket register──▶ API_Server  (agent_key → JWT)
Agent ──heartbeat (10~30s)──▶ API_Server

Trigger fires → API_Server ──execute(AgentCommand)──▶ Agent
                                                       │
                                                       ├─ RSA 복호화 (자격증명)
                                                       ├─ Node.execute (VPC 내부)
                                                       └─ status_update / execution_result
                                                          (메타데이터만, 원본 데이터 VPC 잔류)
```

### 3. Webhook 트리거
```
외부 서비스 ──POST /webhooks/{workflow_id}/{path}──▶ API_Server
                (HMAC 서명 검증)                      │
                                                      └─▶ 실행 디스패치 (위 1 또는 2)
```

## 플러그인 확장 포인트

- **새 노드 추가**: `Execution_Engine/src/nodes/` 에 `BaseNode` 상속 클래스 작성 → `registry.register()` → `tests/nodes/test_{name}.py` 필수
- **새 Repository 구현**: `Database/src/repositories/` 에 ABC 구현, 테스트에서 `InMemoryXxxRepository`로 대체 가능해야 함

## 관련 문서

- 설계 결정 배경: [`decisions.md`](./decisions.md)
- 파일/디렉토리 맵: [`MAP.md`](./MAP.md)
