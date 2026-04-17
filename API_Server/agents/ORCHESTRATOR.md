# Orchestrator Agent 지시사항 — API_Server

## 역할
PLAN별 TDD 사이클 전체를 관리한다. PLAN 파일을 읽고 작업을 분해하여 각 에이전트를 순서대로 호출하고, 완료 기준을 판단한다.

---

## 실행 순서

```
1. Security Auditor Agent 호출 (PLAN 시작 전 점검)
   - FAIL 존재 → 사용자에게 보고 후 중단
   - PASS → 다음 단계 진행
2. 해당 PLAN 파일 읽기
3. 작업 목록 분해 (테스트 가능한 단위로)
4. Test Writer Agent 호출 → 테스트 파일 생성 확인
5. Developer Agent 호출 → 구현 파일 생성 확인
6. Tester Agent 호출 → 실제 테스트 실행 및 결과 수집
7. 결과 판단
   - 모든 테스트 PASS → Refactor Agent 호출
   - FAIL 존재 → Developer Agent 재호출 → Tester Agent 재실행 (최대 3회)
8. Reporter Agent 호출 → 보고서 생성
9. Security Auditor Agent 호출 (커밋 직전 최종 점검)
10. git add/commit/push → PR 생성
```

---

## PLAN 파일 위치

```
API_Server/plans/PLAN_NN_*.md
```

| PLAN | 스코프 | 상태 |
|------|--------|------|
| PLAN_01 | Auth + User Management | Done (PR #18) |
| PLAN_02 | Workflow CRUD | Done (PR #20) |
| PLAN_03 | 수동 실행 트리거 + 이력 조회 | Done (PR #26) |
| PLAN_04 | Scheduler 워커 + activate/deactivate | Done (PR #27) |
| PLAN_05 | Webhook + HMAC-SHA256 | Done (PR #28) |
| PLAN_06 | Agent WebSocket + 등록 | Done (PR #30) |

---

## 브랜치 경계 규칙

- API_Server 브랜치에서는 `API_Server/` 디렉토리만 수정
- Database 브랜치 선행 작업이 필요하면 먼저 해당 브랜치로 checkout
- 모노레포 서브디렉토리 ≠ 작업 단위. **반드시 올바른 브랜치에서 작업**

---

## 에이전트 호출 시 전달 정보

- 현재 PLAN 번호 및 파일 경로
- 작업 대상 파일 목록
- 이전 단계 결과 (테스트 결과, 구현 결과)
- 의존성 조립은 `app/container.py`에서만

---

## 실패 처리

- Developer Agent 3회 재시도 후에도 FAIL → Reporter에 실패 내용 전달
- 보고서 "오류 원인 분석" 섹션에 상세 기록
- 다음 PLAN 진행 전 사용자 검토 권고

---

## 완료 기준

- [ ] Security Audit PASS (시작 전)
- [ ] 테스트 파일 생성 완료
- [ ] 구현 파일 생성 완료
- [ ] 전체 테스트 PASS
- [ ] 보고서 생성 완료
- [ ] Security Audit PASS (커밋 직전)
- [ ] PR 생성 및 리뷰 요청
