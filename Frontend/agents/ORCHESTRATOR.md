# Orchestrator Agent 지시사항 — Frontend

## 역할
Frontend PLAN 별 TDD 사이클 전체를 관리한다. PLAN 파일을 읽고 작업을 분해하여 각 에이전트를 순서대로 호출하고, 완료 기준을 판단한다.

---

## 실행 순서

```
1. Security Auditor Agent 호출 (PLAN 시작 전 점검)
   - FAIL 존재 → 사용자에게 보고 후 중단
   - PASS → 다음 단계 진행
2. 해당 PLAN 파일 읽기
3. 작업 목록 분해 (테스트 가능한 단위로 — 보통 라우트/컴포넌트/스토어 단위)
4. Test Writer Agent 호출 → Playwright 스펙 작성 확인
5. Developer Agent 호출 → 컴포넌트/스토어/클라이언트 구현 확인
6. Tester Agent 호출 → tsc + lint + build + Playwright 실행 및 결과 수집
7. 결과 판단
   - 모든 단계 PASS → Refactor Agent 호출
   - FAIL 존재 → Developer Agent 재호출 → Tester Agent 재실행 (최대 3회)
8. Reporter Agent 호출 → 보고서 생성
9. Security Auditor Agent 호출 (커밋 직전 최종 점검)
10. git add / commit (메시지에 PR 번호 의존성 명시) / push → PR 생성
```

---

## PLAN 파일 위치

```
Frontend/plans/PLAN_NN_*.md
AI_Agent/plans/PLAN_12_skill_bootstrap.md   # Frontend 의 W2-5 / W2-6 / W3-1 항목 포함
```

| PLAN | 스코프 | 상태 |
|------|--------|------|
| PLAN_01 | Workflow Editor MVP (PR A/B/C) | Done |
| PLAN_02 | AI Composer (PR A/B/C/D) | Done |
| PLAN_12 W2-5 | Skill bootstrap interview wizard | Done (PR #137) |
| PLAN_12 W2-6 | Skill review cards + approve/reject | Done (PR #138) |
| PLAN_12 W3-1 | Document upload UI | 미착수 (05/05 부재 후) |

---

## 브랜치 경계 규칙

- **Frontend 브랜치에서는 `Frontend/` 디렉토리만 수정** — 모노레포 서브디렉토리 ≠ 작업 단위
- API_Server / AI_Agent 콘트랙트 변경이 필요하면 먼저 해당 브랜치로 checkout 후 별도 PR
- 메모리 `feedback_no_merge_commits_in_branch.md` 준수: main 동기화는 `git rebase origin/main` (NOT `git merge`). PR 머지 후 재작업은 `git reset --hard origin/main` 부터

---

## 에이전트 호출 시 전달 정보

- 현재 PLAN 번호 + 파일 경로
- 작업 대상 라우트 / 컴포넌트 / 스토어 목록
- 이전 단계 결과 (Playwright 결과, 구현 결과, 라우트 사이즈)
- API 콘트랙트 의존성 (예: `API_Server/app/models/skills.py` 미러)

---

## 실패 처리

- Developer Agent 3회 재시도 후 FAIL → Reporter 에 실패 내용 전달, 사용자 검토 요청
- Playwright 가 race condition 으로 간헐 실패 → mock 응답에 `await route.fulfill` 명시 + assertion 에 `await` 누락 검사
- 보고서 "오류 원인 분석" 에 상세 기록

---

## 완료 기준

- [ ] Security Audit PASS (시작 전)
- [ ] Playwright 스펙 작성 완료
- [ ] 컴포넌트 / 스토어 / 클라이언트 구현 완료
- [ ] `tsc --noEmit` / `next lint` / `next build` 모두 green
- [ ] Playwright (mock) 100% PASS
- [ ] 보고서 생성 완료 (`Frontend/reports/PLAN_NN_*.md`)
- [ ] Security Audit PASS (커밋 직전)
- [ ] PR 본문에 콘트랙트 의존 PR 번호 명시 (예: "Depends on PR #135 — `/api/v1/skills/*` 엔드포인트")

---

## API_Server / AI_Agent 콘트랙트 변경 동반 시

Frontend PR 만 머지하면 main 이 깨지는 시나리오 (콘트랙트 미스매치) 회피:

1. API_Server / AI_Agent 콘트랙트 PR 먼저 머지
2. main 으로 reset → Frontend 브랜치 새로 시작 (`feedback_no_merge_commits_in_branch.md`)
3. Frontend PR 본문에 의존 PR 의 머지 SHA 명시 → 리뷰어 추적 가능
