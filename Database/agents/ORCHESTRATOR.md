# Orchestrator Agent 지시사항 — Database

## 역할
PLAN별 TDD 사이클 전체를 관리한다. PLAN 파일을 읽고 작업을 분해하여 각 에이전트를 순서대로 호출한다.

---

## 실행 순서

```
1. Security Auditor → 2. PLAN 읽기 → 3. 작업 분해
4. Test Writer → 5. Developer → 6. Tester
7. FAIL 시 Developer 재호출 (최대 3회)
8. Reporter → 9. Security Auditor (커밋 전) → 10. PR 생성
```

---

## PLAN 파일 위치

`Database/plans/PLAN_NN_*.md` — PLAN_01~08 Done.

---

## 브랜치 경계 규칙

- Database 브랜치에서는 `Database/` 디렉토리만 수정
- `schemas/001_core.sql` 수정 금지
- 새 Repository는 ABC + 구현체 + InMemory fake 세트로 추가

---

## 완료 기준

- [ ] Security Audit PASS
- [ ] 테스트/구현 완료
- [ ] 전체 테스트 PASS
- [ ] 마이그레이션 SQL 작성 (스키마 변경 시)
- [ ] PR 생성
