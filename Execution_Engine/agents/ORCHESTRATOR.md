# Orchestrator Agent 지시사항 — Execution_Engine

## 역할
PLAN별 TDD 사이클 전체를 관리한다.

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

`Execution_Engine/plans/PLAN_NN_*.md`

| PLAN | 스코프 | 상태 |
|------|--------|------|
| PLAN_01 | BaseNode + NodeRegistry + HttpRequestNode | Done (PR #31) |
| PLAN_02 | DAG executor (Kahn level-sort + gather) | Done (PR #32) |
| PLAN_03 | Celery dispatcher (serverless mode) | Done (PR #33) |
| PLAN_04 | Agent daemon (WebSocket client + WS repo) | Done (PR #34) |
| PLAN_05 | ConditionNode + CodeNode + RestrictedPython | Done (PR #35) |

---

## 브랜치 경계 규칙

- Execution_Engine 브랜치에서는 `Execution_Engine/` 디렉토리만 수정
- Database 선행 작업 필요 시 해당 브랜치로 checkout 먼저

---

## 테스트 실행 규칙 (MANDATORY)

- 테스트 프로세스는 항상 1개만 — 재실행 전 `taskkill //F //IM python.exe`
- background 실행 금지
- `while True: pass` 등 무한루프 테스트 금지

---

## 완료 기준

- [ ] Security Audit PASS
- [ ] 테스트/구현 완료
- [ ] 전체 테스트 PASS
- [ ] PR 생성
