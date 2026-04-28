# Database — Claude Code 브랜치 지침

> 루트 `CLAUDE.md` 보안 규칙과 함께 적용된다.

## 모듈 역할

**Data Layer** — 워크플로우 자동화 엔진의 영속성 계층.
PostgreSQL 스키마 설계, Repository 구현체, 자격증명 암호화 저장소를 담당한다.

`API_Server`와 `Execution_Engine`이 이 브랜치의 Repository 인터페이스를
통해서만 DB에 접근한다 (직접 SQL 금지).

## 파일 위치 규칙 (MANDATORY)

**PLAN_00 이후 Database 는 `auto-workflow-database` 파이썬 패키지로 배포**
된다. 타 브랜치(`API_Server`, `Execution_Engine`)는 `pip install -e Database/`
로 editable 설치하고 `from auto_workflow_database.repositories.base import ...`
로 참조한다. Phase 2 에서 GitHub Packages wheel 게시로 전환 예정.

```
Database/
├── pyproject.toml                  ← 패키지 메타데이터 + 의존성
├── schemas/                         ← DDL (CREATE TABLE/INDEX) SQL
├── migrations/                      ← 스키마 변경 이력 (YYYYMMDD_설명.sql)
├── auto_workflow_database/          ← 파이썬 패키지 루트
│   ├── repositories/
│   │   ├── workflow_repository.py
│   │   ├── execution_repository.py
│   │   └── credential_store.py
│   ├── models/                      ← SQLAlchemy ORM
│   └── crypto/                      ← hybrid.py (ADR-013)
├── scripts/                         ← migrate.py, roll_partitions.py
├── tests/                           ← pytest
└── plans/                           ← PLAN 문서
```

| 파일 종류 | 저장 위치 | import 경로 |
|-----------|-----------|------------|
| `CREATE TABLE`, `CREATE INDEX` | `schemas/` | — |
| `ALTER TABLE`, 컬럼 변경 | `migrations/YYYYMMDD_*.sql` | — |
| Repository 구현 | `auto_workflow_database/repositories/` | `auto_workflow_database.repositories.X` |
| SQLAlchemy ORM 모델 | `auto_workflow_database/models/` | `auto_workflow_database.models.X` |
| 암호 헬퍼 | `auto_workflow_database/crypto/` | `auto_workflow_database.crypto.X` |
| 마이그레이션 실행 스크립트 | `scripts/` | (직접 실행) |
| pytest | `tests/` | — |

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
| `skills` | PLAN_12/ADR-022 Skill Bootstrap — 코드화된 팀 정책 (condition+action) |
| `skill_sources` | 각 skill 의 출처 추적 (document/conversation/observation) — append-only |
| `skill_applications` | compose 시 skill 적용 감사 (workflow_id 는 hard FK 아님) — append-only |
| `policy_documents` | 업로드 SOP/핸드북. (owner_user_id, content_hash) UNIQUE 로 중복 차단 |
| `policy_extractions` | 청크 + BGE-M3 임베딩 (`vector(1024)`, HNSW 인덱스) |

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
