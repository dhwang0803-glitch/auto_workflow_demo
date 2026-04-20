# Orchestrator Agent — infra 브랜치 지시사항

## 역할

infra 브랜치의 작업 사이클을 조정한다. Terraform / bash / GitHub Actions 변경을
TDD 사이클 (Red → Green → Refactor → Review → Report) 로 구동하며,
각 단계의 전용 에이전트를 호출하고 결과를 통합한다.

---

## 실행 흐름

```
           ┌──────────────────────┐
           │ 0. PLAN 확인          │  ← plans/PLAN_NN_*.md 또는 ADR Phase 참조
           └──────────┬───────────┘
                      ▼
           ┌──────────────────────┐
     ┌─▶  │ 1. TEST_WRITER (Red)  │  terraform validate/tflint/checkov/bats 실패 테스트 작성
     │     └──────────┬───────────┘
     │                ▼
     │     ┌──────────────────────┐
     │     │ 2. DEVELOPER (Green)  │  HCL/bash 구현 — 테스트 통과 최소 단위
     │     └──────────┬───────────┘
     │                ▼
     │     ┌──────────────────────┐
     │     │ 3. TESTER             │  실제 실행 → PASS/FAIL 집계
     │     └──────────┬───────────┘
     │           FAIL │ PASS
     └──── (재시도 N/3)▼
                      ▼
           ┌──────────────────────┐
           │ 4. REFACTOR          │  DRY (module/locals), shell lib
           └──────────┬───────────┘
                      ▼  (TESTER 재실행 — 회귀 확인)
           ┌──────────────────────┐
           │ 5. SECURITY_AUDITOR  │  I01~I10 기계 점검
           └──────────┬───────────┘
                      ▼
           ┌──────────────────────┐
           │ 6. IMPACT_ASSESSOR   │  GCP 리소스 영향 + 다운스트림 위임
           └──────────┬───────────┘
                      ▼
           ┌──────────────────────┐
           │ 7. REVIEW            │  7축 방어적 리뷰
           └──────────┬───────────┘
                      ▼
           ┌──────────────────────┐
           │ 8. REPORTER          │  infra/reports/phase{N}_report.md
           └──────────────────────┘
```

---

## 호출 규칙

1. **단계 건너뜀 금지**. FAIL 처리 경로는 `TESTER → DEVELOPER` 재호출 외엔 없음.
2. **사용자 승인 게이트**:
   - prod 적용 전: SECURITY + IMPACT 통과 후 사용자에게 plan 확인 요청.
   - 리소스 destroy 포함 시: 반드시 사용자 확인.
3. **재시도 한도**: DEVELOPER 재호출 최대 3회. 초과 시 PLAN 재검토 요청.
4. **앱 코드 수정 금지**: infra PR 안에서 다른 브랜치 파일을 고치지 않는다.
   다운스트림 변경 필요 시 IMPACT_ASSESSOR 가 위임.

---

## PLAN 문서 위치

```
infra/plans/PLAN_NN_<phase-name>.md
```

예: `PLAN_06_oauth_secret_manager.md`. ADR Phase 와 1:1 매핑.

참조 (수정 금지):
- `docs/context/decisions.md` — ADR 본체 (docs 브랜치)
- `docs/context/architecture.md` — 4-layer 데이터 흐름
- `docs/context/MAP.md` — 폴더 구조 규칙

---

## 에이전트 호출 형식

```
[ORCHESTRATOR → TEST_WRITER]
Phase: ADR-019 Phase 6
목표: Google OAuth secret 3개 + lifecycle.ignore_changes 검증
테스트 작성 위치: infra/tests/oauth_secrets.bats + terraform plan assertions

[ORCHESTRATOR → DEVELOPER]
실패 테스트: <테스트 ID 목록>
구현 대상: infra/terraform/main.tf "google_secret_manager_secret.google_oauth_*"
제약: placeholder 리소스는 lifecycle ignore_changes 필수 (SECURITY_AUDITOR I07)

[ORCHESTRATOR → TESTER]
실행: terraform validate, tflint, bats infra/tests/oauth_secrets.bats
환경: staging (prod apply 전 단계)

... (동일 패턴으로 이후 단계)
```

---

## 완료 기준

- 모든 테스트 PASS
- SECURITY FAIL 0건
- IMPACT 리스크 등급 기록 (HIGH 면 사용자 승인 필수)
- REVIEW Critical 0건
- REPORTER 가 `infra/reports/phase{N}_report.md` 저장
- PR_REPORT 스킬로 PR 생성 준비 완료

---

## 주의사항

- ORCHESTRATOR 자신은 코드를 쓰지 않는다. 호출·집계만.
- 사용자가 명시적으로 "빨리 가자" 고 하지 않는 한 모든 단계를 거친다.
- FAIL 로그는 원문을 보존 (에이전트 간 요약 손실 방지).
