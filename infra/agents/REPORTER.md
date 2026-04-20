# Reporter Agent — infra 브랜치 지시사항

## 역할

infra TDD 사이클이 완료된 후 Phase 별 결과 보고서를 생성한다.
ORCHESTRATOR / TEST_WRITER / DEVELOPER / TESTER / REFACTOR / SECURITY_AUDITOR /
IMPACT_ASSESSOR / REVIEW 로부터 결과를 수집하여 표준 형식으로 문서화한다.

Phase 구분은 ADR (주로 ADR-018/019/020/021) 의 Phase N 과 1:1 매핑된다.
예: ADR-019 Phase 6 → `infra/reports/adr019_phase6_report.md`.

---

## 보고서 저장 위치

```
infra/reports/<adr>_phase{N}_report.md
```

예:
- `infra/reports/adr018_phase1_report.md` — Cloud SQL 초기 프로비저닝
- `infra/reports/adr019_phase6_report.md` — OAuth Secret Manager 주입
- `infra/reports/adr021_phase1_report.md` — (pending) Worker 배포 경로

단일 ADR 없이 독립 작업이면 `infra/reports/YYYY-MM-DD_<slug>.md` 형식.

---

## 보고서 표준 형식

```markdown
# <ADR-NNN> Phase {N} — <phase 이름> 결과 보고서

**대상**: ADR-NNN Phase N (`docs/context/decisions.md` 참조)
**작성일**: YYYY-MM-DD
**상태**: PASS 완료 / FAIL 잔존 / 사용자 승인 대기

---

## 1. 변경 결과

### 변경된 파일
| 파일 | 위치 | 설명 |
|------|------|------|
| main.tf | infra/terraform/ | google_secret_manager_secret.google_oauth_* 3개 |
| inject_oauth_secrets.sh | infra/scripts/ | stdin pipe 주입 스크립트 |
| ... | ... | ... |

### 주요 구현 내용
- (bullet 3~5개)

---

## 2. 테스트 결과 (TESTER)

### 요약
| 단계 | 도구 | 건수 | PASS | FAIL | SKIP |
|------|------|------|------|------|------|
| Phase A 정적 | terraform validate | 1 | 1 | 0 | 0 |
| Phase A 정적 | tflint | N | ... | ... | ... |
| Phase A 정적 | checkov | N | ... | ... | ... |
| Phase A 정적 | shellcheck | N | ... | ... | ... |
| Phase A 정적 | actionlint | N | ... | ... | ... |
| Phase B 단위 | bats | N | ... | ... | ... |
| Phase C plan | terraform plan | add=N change=N destroy=N | | | |
| Phase D live | staging apply + smoke | (실행/SKIP) | | | |

### 상세 FAIL (해결됨)
| 항목 | 원인 | 해결 |
|------|------|------|
| tflint terraform_required_providers | provider version 누락 | versions.tf 에 google ~> 6.0 고정 |
| ... | ... | ... |

---

## 3. 리팩토링 (REFACTOR)

| 파일 | 변경 유형 | 변경 전 → 후 | plan-diff |
|------|----------|-------------|-----------|
| main.tf | locals 추출 | project_id 5회 반복 → local.project | no-change |
| scripts/ | lib 분리 | proxy 기동 3개 스크립트 중복 → lib/proxy.sh | (N/A) |

해당 없음이면 "리팩토링 없음 (조기 추상화 방지)".

---

## 4. 보안 감사 (SECURITY_AUDITOR)

| 규칙 | 결과 |
|------|------|
| I01 tfvars 실값 커밋 | PASS |
| I02 tfstate 커밋 | PASS |
| I03 HCL 시크릿 하드코딩 | PASS |
| I04 프로젝트 ID 하드코딩 | PASS |
| I05 gcloud stdout 유출 | PASS |
| I06 GH Actions secret 로그 | PASS |
| I07 deletion_protection / ignore_changes | PASS |
| I08 Ruleset bypass (WARN) | (기록) |
| I09 .gitignore 필수 항목 | PASS |
| I10 IAM 최소권한 (WARN) | (해당 없음) |

FAIL 항목이 있으면 이 보고서 작성 전 해결됐는지 명시.

---

## 5. 사후 영향 평가 (IMPACT_ASSESSOR)

- **리스크 등급**: 🔴 HIGH / 🟡 MEDIUM / 🟢 LOW
- **근거**: (1줄)
- **terraform plan**: add=N change=N destroy=N
- **다운스트림 영향**:
  | 브랜치 | 영향 | 후속 PR |
  |--------|------|---------|
  | API_Server | ✅/➖ | <PR 번호 또는 없음> |
  | Database | ✅/➖ | |
  | Execution_Engine | ✅/➖ | |
- **롤백 계획**:
  - [ ] staging 선 검증 완료
  - [ ] Secret 이전 version: `<name>:vN`
  - [ ] tfstate 로컬 스냅샷 경로

---

## 6. 리뷰 (REVIEW)

- Critical: N건 (처리 완료)
- Major: N건 (처리 / 잔존)
- Minor: N건 (후속 과제로 기록)

잔존 항목:
- [Major] <파일:라인> — <내용> — <후속 ADR/Issue>

---

## 7. 사용자 승인 기록

- prod apply 승인: YES/NO (타임스탬프)
- destroy 승인: YES/NO
- 특이사항: (있으면 기재)

---

## 8. 다음 Phase 권고사항

- (다음 Phase 진행 전 확인 필요 사항)
- (선행 의존성)
- (관측해야 할 운영 지표)
```

---

## 수집해야 할 정보 및 출처

| 섹션 | 출처 |
|------|------|
| 변경 결과 | DEVELOPER 결과 + `git diff --stat main...HEAD` |
| 테스트 결과 | TESTER Phase A/B/C/D 출력 |
| 리팩토링 | REFACTOR 항목 목록 |
| 보안 감사 | SECURITY_AUDITOR I01~I10 결과 |
| 사후 영향 평가 | IMPACT_ASSESSOR 보고서 |
| 리뷰 | REVIEW 출력의 Findings |
| 사용자 승인 | ORCHESTRATOR 가 수집한 승인 로그 |
| 다음 Phase 권고 | PLAN / ADR Phase 항목 + 이번 Phase 이슈 |

---

## 보고서 작성 완료 후

- [ ] `infra/reports/<adr>_phase{N}_report.md` 저장 확인
- [ ] 해당 PLAN 파일의 "진행 체크리스트" 해당 항목 체크
- [ ] ORCHESTRATOR 에 완료 보고 → PR 생성 단계(PR_REPORT 스킬) 로 전환
- [ ] PR 본문 "사후 영향 평가" 섹션에 본 보고서 요약 링크

---

## 주의사항

1. 시크릿 값 / tfstate 내용 / 실제 프로젝트 수치(비용 등) 를 보고서에 포함하지
   않는다. 이름·리스크 등급·건수만 기록.
2. FAIL 이 잔존한 상태로는 보고서를 "PASS 완료" 로 작성하지 않는다 —
   `상태: FAIL 잔존` 로 명시.
3. 다음 Phase 권고에 "다음 Phase 에서 앱 코드 변경 필요" 가 있으면 해당 브랜치
   담당자 태그 포함.
4. 보고서는 infra 브랜치 PR 에 포함된다. docs 브랜치 대상 아님.
