# Refactor Agent 지시사항 — Database

## 역할
모든 테스트가 PASS된 이후에만 실행. 테스트 통과 상태를 유지하면서 코드 품질 개선.

---

## 핵심 원칙

1. **테스트 통과 유지**: 리팩토링 후 전체 테스트 재실행 PASS 확인
2. **기능 변경 금지**: 동작 결과가 달라지면 안 됨
3. **범위 제한**: `auto_workflow_database/` 코드만 수정
4. **작은 단위**: 한 번에 하나씩 개선 후 테스트 확인

---

## 검토 항목

### 코드 품질
- [ ] 1회용 헬퍼 → 인라인 처리
- [ ] 중복 DTO 변환 로직 통합
- [ ] JSONB 변경 시 `flag_modified()` 누락 확인
- [ ] `DateTime(timezone=True)` 통일 여부

### 성능
- [ ] N+1 쿼리 패턴 → 배치 조회
- [ ] 인덱스 누락 확인
- [ ] pool 설정 적절성 (`_session.py`)

### 일관성
- [ ] Repository ABC와 구현체 시그니처 일치
- [ ] InMemory fake가 실제 구현과 동일 동작

---

## 범위 제외

- `tests/`, `plans/`, `schemas/001_core.sql`, `.env`

---

## 완료 후

1. `taskkill` 후 전체 테스트 재실행
2. PASS/FAIL 건수 동일 확인
3. 변경 내용 → Reporter Agent에 전달
