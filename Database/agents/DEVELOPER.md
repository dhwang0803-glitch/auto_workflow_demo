# Developer Agent 지시사항 — Database

## 역할
Test Writer Agent가 작성한 테스트를 통과하는 최소한의 코드를 구현한다 (TDD Green 단계).
과도한 설계나 불필요한 기능을 추가하지 않는다.

---

## 구현 원칙

1. **테스트 통과 최우선**: 현재 실패하는 테스트를 통과시키는 것만 구현한다
2. **최소 구현**: 테스트를 통과하는 가장 단순한 코드를 작성한다
3. **CLAUDE.md 준수**: `Database/CLAUDE.md` 파일 위치 규칙을 벗어나지 않는다
4. **함수 증식 금지**: 1회용 헬퍼/thin wrapper 만들지 않는다

---

## 파일 위치

| 파일 종류 | 위치 | import 경로 |
|-----------|------|------------|
| DDL (CREATE TABLE/INDEX) | `schemas/` | — |
| 스키마 변경 (ALTER TABLE) | `migrations/YYYYMMDD_*.sql` | — |
| Repository 구현 | `auto_workflow_database/repositories/` | `auto_workflow_database.repositories.X` |
| ORM 모델 | `auto_workflow_database/models/` | `auto_workflow_database.models.X` |
| 암호화 헬퍼 | `auto_workflow_database/crypto/` | `auto_workflow_database.crypto.X` |
| 마이그레이션 스크립트 | `scripts/` | (직접 실행) |
| pytest | `tests/` | — |

**`Database/` 루트에 `.py` 파일 직접 생성 금지.**

---

## Repository 패턴

ABC 인터페이스(`base.py`)와 Postgres 구현체를 분리. 테스트에서는 `InMemory*Repository` fake 사용.

---

## DB 접근 원칙

1. **비동기 전용**: `create_async_engine` + `asyncpg`
2. **N+1 금지**: 루프 안에서 DB 쿼리 절대 금지
3. **pool 설정**: `_session.py`의 `build_engine()`에서 일원화
4. **JSONB 변경**: `flag_modified()` 필수

---

## datetime 통일

- ORM: `DateTime(timezone=True)` 필수
- Python: `datetime.now(timezone.utc)` 사용
- `schemas/001_core.sql` **수정 금지** — migration 1이 `\i`로 참조
- 컬럼 추가/변경은 `migrations/` 파일로만

---

## 구현 완료 후 자가 점검

- [ ] 하드코딩된 DB URL, 비밀번호 없음
- [ ] N+1 쿼리 없음
- [ ] DateTime(timezone=True) 통일
- [ ] 새 Repository는 ABC + InMemory fake 세트로 추가
- [ ] 스키마 변경은 `migrations/` 파일로만
