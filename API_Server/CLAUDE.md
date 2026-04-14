# API_Server — Claude Code 브랜치 지침

> 루트 `CLAUDE.md` 보안 규칙과 함께 적용된다.

## 모듈 역할

**FastAPI Core Server** — 워크플로우 자동화 엔진의 두뇌.
Frontend로부터 워크플로우 JSON을 받아 CRUD, DAG 스케줄링, 트리거 감시,
Execution_Engine 및 Agent로의 실행 디스패치를 조율한다.

4-레이어 아키텍처 중 **Core Layer**를 담당하며, `Database`(저장소)와
`Execution_Engine`(실행기)을 오케스트레이션한다.

## 파일 위치 규칙 (MANDATORY)

```
API_Server/
├── app/
│   ├── routers/    ← 엔드포인트별 라우터 (직접 실행 X)
│   │   ├── workflows.py    ← CRUD, 실행 트리거
│   │   ├── executions.py   ← 실행 이력 조회
│   │   ├── agents.py       ← Agent 등록/WebSocket
│   │   └── webhooks.py     ← 동적 Webhook 트리거 수신
│   ├── services/   ← 비즈니스 로직 (직접 실행 X)
│   │   ├── workflow_service.py   ← WorkflowService (조율자)
│   │   ├── dag_scheduler.py      ← DAGScheduler (Kahn 위상정렬)
│   │   ├── trigger_manager.py    ← Webhook/Cron/Polling 감시
│   │   └── agent_manager.py      ← WebSocket Agent 연결 관리
│   ├── models/     ← Pydantic 요청/응답 + WorkflowSchema
│   └── main.py     ← FastAPI 앱 진입점 (DI 조립)
├── tests/          ← pytest (httpx TestClient)
└── config/         ← 환경별 설정 yaml (.env.example 포함)
```

| 파일 종류 | 저장 위치 |
|-----------|-----------|
| REST 라우터 | `app/routers/` |
| Core 비즈니스 로직 | `app/services/` |
| Pydantic 스키마 (`WorkflowSchema`, `NodeConfig` 등) | `app/models/` |
| FastAPI 앱 + `create_app()` DI 조립 | `app/main.py` |
| pytest | `tests/` |

**`API_Server/` 루트 또는 프로젝트 루트에 `.py` 파일 직접 생성 금지.**

## 기술 스택

```python
from fastapi import FastAPI, Depends, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sqlalchemy                 # Database 브랜치와 공유
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # Cron 트리거
from jose import jwt              # Agent JWT 인증
import uvicorn
```

## 핵심 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/api/v1/workflows` | 워크플로우 생성 (순환 참조 검사) |
| GET | `/api/v1/workflows/{id}` | 워크플로우 조회 |
| POST | `/api/v1/workflows/{id}/activate` | 트리거 등록 (활성화) |
| POST | `/api/v1/workflows/{id}/execute` | 수동 실행 |
| GET | `/api/v1/executions/{id}` | 실행 이력 조회 |
| POST | `/api/v1/agents/register` | Agent 등록 (agent_key → JWT) |
| WS | `/api/v1/agents/ws` | Agent 상시 연결 (명령 push, heartbeat) |
| POST | `/webhooks/{workflow_id}/{path}` | 동적 Webhook 트리거 |

## 실행 모드 디스패치

`WorkflowService.execute_workflow()`는 `workflow.settings.execution_mode`에 따라 분기:

- `"serverless"` → `Execution_Engine`의 Celery Worker로 태스크 큐잉 (Light/Middle 유저)
- `"agent"` → `AgentManager`를 통해 고객 VPC의 Agent로 WebSocket 전송 (Heavy 유저)

## 실행

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 인터페이스

- **업스트림**: Frontend (워크플로우 JSON 수신), Agent (Heartbeat/결과 수신), 외부 Webhook
- **다운스트림**:
  - `Database` — 워크플로우/실행이력/자격증명 저장소
  - `Execution_Engine` — Celery 큐를 통한 서버리스 실행 위임
  - Agent — WebSocket으로 AgentCommand 전송

## 보안 주의사항

- 자격증명(Credentials)은 **절대** 평문으로 라우터/서비스 코드에 넘기지 않는다.
  `CredentialStore.retrieve()`는 실행 시점에만 호출.
- Agent로 전달 시 **공개키 암호화** 후 전송 (Agent만 복호화 가능).
- Webhook 엔드포인트는 HMAC 서명 검증 필수.
