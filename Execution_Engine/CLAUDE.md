# Execution_Engine — Claude Code 브랜치 지침

> 루트 `CLAUDE.md` 보안 규칙과 함께 적용된다.

## 모듈 역할

**Execution Layer** — 실제로 워크플로우의 노드를 실행하는 엔진.
두 가지 실행 모드를 모두 지원한다:

1. **Serverless Worker**: Celery + Redis 큐 → Cloud Run 컨테이너에서 실행 (Light/Middle 유저)
2. **Agent**: 고객 VPC에 설치되는 경량 실행기. 중앙 서버와 WebSocket으로 연결 (Heavy 유저)

두 모드 모두 동일한 `BaseNode` 플러그인 인터페이스와 `NodeRegistry`를 공유한다.

## 파일 위치 규칙 (MANDATORY)

```
Execution_Engine/
├── src/
│   ├── nodes/            ← BaseNode 구현체 (플러그인, import 전용)
│   │   ├── base.py           ← BaseNode ABC
│   │   ├── http_request.py   ← HttpRequestNode
│   │   ├── condition.py      ← ConditionNode
│   │   ├── code.py           ← CodeExecutionNode (샌드박스 필수)
│   │   └── registry.py       ← NodeRegistry
│   ├── dispatcher/       ← 실행 디스패처
│   │   ├── serverless.py     ← Celery 태스크 디스패치
│   │   └── agent_client.py   ← WebSocket 클라이언트 (Agent → 서버)
│   ├── runtime/          ← DAG 실행 런타임
│   │   ├── executor.py       ← 단계별 노드 실행 (asyncio.gather 병렬)
│   │   └── sandbox.py        ← RestrictedPython/Docker 격리
│   └── agent/            ← Agent 데몬 (고객 VPC 설치용)
│       ├── main.py           ← Agent 진입점
│       ├── heartbeat.py      ← 생존 신호 송신
│       └── command_handler.py ← 서버 명령 수신/실행
├── scripts/      ← worker.py, agent_run.py (직접 실행)
├── tests/        ← pytest (노드별 단위 테스트 + 통합)
├── config/       ← Celery 설정, 노드 기본 파라미터
└── docs/         ← 노드 개발 가이드, 샌드박스 설계 문서
```

| 파일 종류 | 저장 위치 |
|-----------|-----------|
| 노드 구현 (`BaseNode` 상속) | `src/nodes/` |
| Celery 태스크 / Agent 클라이언트 | `src/dispatcher/` |
| DAG 실행 런타임 | `src/runtime/` |
| Agent 데몬 (고객 VPC 배포) | `src/agent/` |
| Celery Worker 실행 | `scripts/worker.py` |
| Agent 실행 | `scripts/agent_run.py` |
| pytest | `tests/` |

**`Execution_Engine/` 루트 또는 프로젝트 루트에 `.py` 파일 직접 생성 금지.**

## 기술 스택

```python
from celery import Celery
import redis.asyncio as redis
import httpx                      # HTTP 노드
import websockets                 # Agent ↔ Server
from RestrictedPython import compile_restricted  # 코드 노드 샌드박스
from cryptography.hazmat.primitives.asymmetric import rsa  # Agent 키페어
```

## 플러그인 확장 (새 노드 추가)

```python
from src.nodes.base import BaseNode
from src.nodes.registry import registry

class SlackSendMessageNode(BaseNode):
    @property
    def node_name(self) -> str:
        return "slack_send_message"

    async def execute(self, input_data, parameters):
        ...

registry.register(SlackSendMessageNode)
```

노드 추가 시 `tests/nodes/test_{노드명}.py` 작성 필수.

## 실행 모드별 진입점

```bash
# Serverless Worker (우리 클라우드)
python scripts/worker.py --queue workflow_tasks --concurrency 10

# Agent (고객 VPC에 배포)
python scripts/agent_run.py --agent-key <KEY> --server-url wss://api.example.com/agents/ws
```

## 샌드박스 (CodeExecutionNode)

**절대 `eval()` / `exec()` 직접 사용 금지.**

- 1차 방어: `RestrictedPython`으로 AST 검사 + 내장 함수 화이트리스트
- 2차 방어: 격리된 Docker 컨테이너 (네트워크/FS 제한)에서 실행
- 실행 타임아웃 필수 (기본 30초)

## Agent 통신 프로토콜

| 방향 | 메시지 | 용도 |
|------|--------|------|
| Agent → Server | `register` | 최초 연결 (agent_key → JWT) |
| Agent → Server | `heartbeat` | 10~30초 주기 생존 신호 |
| Server → Agent | `execute` | AgentCommand (workflow JSON + encrypted creds) |
| Agent → Server | `status_update` | 노드별 실행 상태 |
| Agent → Server | `execution_result` | 최종 결과 (메타데이터만, 대용량 데이터는 VPC 내 유지) |

**멱등성**: 모든 `execute` 메시지는 `execution_id`로 중복 실행을 방지해야 한다.

## 인터페이스

- **업스트림**: `API_Server` — Celery 큐 또는 WebSocket으로 실행 명령 수신
- **다운스트림**:
  - `Database` — 실행 결과 메타데이터 저장 (ExecutionRepository 경유)
  - 외부 서비스 — 노드가 호출하는 실제 API들

## 보안 주의사항

- 자격증명은 **실행 시점에만** 복호화, 노드 파라미터로 주입 후 즉시 폐기
- Agent는 고객 VPC의 내부 데이터를 외부로 유출하지 않아야 함 (메타데이터만 전송)
- 커스텀 코드 노드는 반드시 샌드박스 통과 후 실행
