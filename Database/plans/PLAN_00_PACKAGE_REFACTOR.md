# PLAN_00 — Database 를 `auto-workflow-database` 파이썬 패키지로 분리

> **브랜치**: `Database` · **작성일**: 2026-04-15 · **완료일**: 2026-04-15 · **상태**: Done
>
> API_Server / Execution_Engine 착수를 앞두고, Database 를 sys.path 의존적
> monorepo 임포트(`Database.src.*`) 에서 정식 파이썬 패키지로 전환한다.
> 기능 변경 0, 구조 변경 전부.

## 1. 배경

현재까지 타 브랜치는 `from Database.src.repositories.base import ...` 로
참조하도록 되어 있었는데, 이는:

- Repo 루트가 sys.path 에 있어야만 import 가 풀리는 구조적 취약성
- 타 브랜치가 Database 최신 코드를 받으려면 매번 `git pull origin main` 강제
- 패키지 경계가 불명확 (internal helper 도 외부에서 import 가능)

API_Server 가 Repository 를 직접 쓸 예정이라 이 문제를 지금 해결해야 한다.

## 2. 결정

**Phase 1 (본 PLAN)**: `pyproject.toml` 기반 editable local install

- `Database/pyproject.toml` 신설 (`auto-workflow-database`, v0.1.0)
- `Database/src/` → `Database/auto_workflow_database/` 물리 이동 (`git mv`)
- 19개 파일의 임포트 경로 일괄 치환 (`Database.src.` → `auto_workflow_database.`)
- 타 브랜치는 `pip install -e Database/` 로 설치

**Phase 2 (후속, 시점 미정)**: GitHub Packages wheel 게시

- Database 릴리스마다 CI 가 wheel 빌드 → GitHub Packages 에 push
- 타 브랜치는 버전 핀 (`auto-workflow-database==0.2.1`)
- API_Server 코드의 `import` 문은 **Phase 1 → 2 전환 시 한 줄도 바뀌지 않음**
  (editable → published 는 설치 소스만 바뀜)

## 3. 범위

**In**
- `pyproject.toml` 작성 (setuptools build backend, 의존성 선언)
- 디렉토리 rename (`src/` → `auto_workflow_database/`) via `git mv`
- 임포트 경로 일괄 치환 (19 파일)
- `conftest.py` sys.path 해킹 제거
- `CLAUDE.md` 의 파일 위치 규칙 갱신
- 전체 테스트 24/24 회귀 검증

**Out**
- Phase 2 CI/게시 파이프라인
- API_Server / Execution_Engine 브랜치의 실제 설치 (각 브랜치 PLAN 에서)
- 새 기능 / 스키마 / 엔드포인트

## 4. 산출물

| 경로 | 내용 |
|------|------|
| `pyproject.toml` | 패키지 메타데이터 + 의존성 선언 |
| `auto_workflow_database/` | 구 `src/` 내용 전부 이동 |
| `conftest.py` | sys.path 해킹 제거, marker 로만 유지 |
| `CLAUDE.md` | 디렉토리 규칙 + import 경로 테이블 갱신 |
| `plans/PLAN_00_PACKAGE_REFACTOR.md` | 본 문서 |

## 5. 수용 기준

- [x] `pip install -e Database/` 가 깨끗이 성공
- [x] `python -c "from auto_workflow_database.repositories.base import CredentialStore"` 통과
- [x] 전체 테스트 24/24 통과 (DB 포함) *(2026-04-15)*
- [x] `Database.src` 를 참조하는 코드가 0건 (git grep)
- [x] `CLAUDE.md` 디렉토리 규칙 블록이 새 구조 반영

## 6. 후속 영향

- **API_Server PLAN_01** — 의존성에 `auto-workflow-database` 추가,
  `pip install -e ../Database` 로 로컬 설치. `from auto_workflow_database...`
  로 ABC 참조
- **Execution_Engine** — 동일 방식
- **Phase 2 전환 시** — `pyproject.toml` 에 wheel 게시 설정 + GH Actions 추가.
  타 브랜치의 의존성 라인만 `file://` 에서 버전으로 교체
