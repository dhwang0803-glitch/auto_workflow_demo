# Database — Claude Code 브랜치 지침

> 루트 `CLAUDE.md` 보안 규칙과 함께 적용된다.

## 모듈 역할

**Data Layer** — 워크플로우 자동화 엔진의 영속성 계층.
PostgreSQL 스키마 설계, Repository 구현체, 자격증명 암호화 저장소를 담당한다.

`API_Server`와 `Execution_Engine`이 이 브랜치의 Repository 인터페이스를
통해서만 DB에 접근한다 (직접 SQL 금지).

## 파일 위치 규칙 (MANDATORY)

```
Database/
├── schemas/      ← DDL (CREATE TABLE/INDEX) SQL
├── migrations/   ← 스키마 변경 이력 (YYYYMMDD_설명.sql)
├── src/          ← Repository 구현체 (import 전용)
│   ├── repositories/
│   │   ├── workflow_repository.py   ← PostgresWorkflowRepository
│   │   ├── execution_repository.py  ← PostgresExecutionRepository
│   │   └── credential_store.py      ← AES-256 암호화 저장소
│   └── models/   ← SQLAlchemy ORM 모델
├── scripts/      ← migrate.py, seed.py, validate.py (직접 실행)
├── tests/        ← pytest (실제 DB 연결, 스키마 검증)
└── docs/         ← ERD, 설계 문서
```

| 파일 종류 | 저장 위치 |
|-----------|-----------|
| `CREATE TABLE`, `CREATE INDEX` | `schemas/` |
| `ALTER TABLE`, 컬럼 변경 | `migrations/YYYYMMDD_*.sql` |
| Repository 구현 (import 전용) | `src/repositories/` |
| SQLAlchemy ORM 모델 | `src/models/` |
| 마이그레이션 실행 스크립트 | `scripts/` |
| pytest | `tests/` |

**`Database/` 루트 또는 프로젝트 루트에 파일 직접 생성 금지.**

## 기술 스택

```python
import sqlalchemy
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
import asyncpg
from cryptography.fernet import Fernet   # 자격증명 암호화
```

- PostgreSQL 16+
- 비동기 드라이버: `asyncpg` (FastAPI async와 호환)
- ORM: SQLAlchemy 2.0 async

## 핵심 테이블

| 테이블 | 설명 |
|--------|------|
| `workflows` | 워크플로우 정의 (JSONB로 nodes/connections 저장) |
| `executions` | 실행 이력 (status, started_at, finished_at, node_results JSONB) |
| `credentials` | 암호화된 자격증명 (owner_id, name, encrypted_data) |
| `users` | 계정 정보 |
| `agents` | 등록된 Agent 메타데이터 (owner_id, public_key, last_heartbeat) |
| `webhook_registry` | 동적 Webhook 경로 ↔ workflow_id 매핑 |

## 핵심 인덱스

```sql
CREATE INDEX idx_executions_workflow_id ON executions(workflow_id, started_at DESC);
CREATE INDEX idx_workflows_owner ON workflows(owner_id) WHERE is_active = true;
CREATE INDEX idx_webhook_path ON webhook_registry(path);
```

## Repository 패턴

`API_Server`는 ABC 인터페이스(`WorkflowRepository`, `ExecutionRepository`,
`CredentialStore`)에만 의존. 이 브랜치는 그 구현체를 제공한다.
테스트 시 `InMemoryWorkflowRepository`로 대체 가능한 구조 유지.

## 자격증명 암호화 규칙

- 저장 시: AES-256 (Fernet) 대칭키 암호화, 키는 환경변수 `CREDENTIAL_MASTER_KEY`
- Agent 모드 전송 시: Agent 공개키(RSA)로 **재암호화**하여 전달
- 평문 자격증명을 **로그/DB/응답**에 절대 포함 금지

## 마이그레이션 파일 네이밍

```
migrations/
├── 20260414_initial_schema.sql
├── 20260420_add_agents_table.sql
└── 20260425_add_webhook_registry.sql
```

## 인터페이스

- **다운스트림**: `API_Server`, `Execution_Engine` — Repository/CredentialStore 구현체 제공
- 스키마 변경 시 `migrations/`에 이력 SQL 추가 후 다운스트림 브랜치에 공지
