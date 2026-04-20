# ADR-021 Phase 6 — Worker Pools Live E2E 결과 보고서

**대상**: ADR-021 Phase 6 (`docs/context/decisions.md` 참조, PLAN_21 §6)
**작성일**: YYYY-MM-DD  _(live E2E 실행 후 기입)_
**상태**: 🟡 스켈레톤 — live E2E 미실행

> 이 보고서는 `infra/docs/RUNBOOK_phase21_e2e.md` 의 Step 1~8 을 실제로
> 실행한 뒤 채워진다. staging apply → /execute 3회 → destroy 까지의
> 관측치를 한 곳에 모은다.

---

## 1. 변경 결과

### 변경된 파일
| 파일 | 위치 | 설명 |
|------|------|------|
| worker.tf | infra/terraform/ | scaling_mode = MANUAL 로 전환 (Python SDK 정합) |
| variables.tf | infra/terraform/ | unused `ee_worker_max_instances` 제거 |
| test_phase_21.bats | infra/tests/ | MANUAL + manual_instance_count 계약 반영 |
| run_e2e_phase21.sh | infra/scripts/ | Phase 6.1 관찰 러너 (steps 4-7) |
| RUNBOOK_phase21_e2e.md | infra/docs/ | Step 1~8 실행 가이드 |

### 주요 구현 내용
- MANUAL scaling 모드 확정 — `google-cloud-run` 0.16.0 SDK 의 `WorkerPoolScaling` 이 `manual_instance_count` 만 노출하여 AUTOMATIC 경로는 Python 에서 patch 불가
- Scale-down 은 현재 `terraform destroy` 에 의존 — 자동 watchdog 은 post-Phase-6 follow-up
- `run_e2e_phase21.sh` 는 read-only (curl + `gcloud logging read`); terraform apply/destroy 는 수동

---

## 2. 테스트 결과 (TESTER)

### 요약
| 단계 | 도구 | 건수 | PASS | FAIL | SKIP |
|------|------|------|------|------|------|
| Phase A 정적 | terraform validate | 1 | 1 | 0 | 0 |
| Phase A 정적 | terraform fmt -check | 1 | 1 | 0 | 0 |
| Phase B 단위 | bats (test_phase_21.bats) | 12 | _TBD_ | _TBD_ | _TBD_ |
| Phase C plan | terraform plan (staging) | _TBD_ | | | |
| Phase D live | run_e2e_phase21.sh (staging) | 3 execs | _TBD_ | _TBD_ | _TBD_ |

### Live E2E 측정 (RUNBOOK Step 4-7)
| 항목 | 값 |
|------|----|
| Step 1 (이미지 빌드+push) 소요 | _분_ |
| Step 2 (terraform apply) 소요 | _분_ |
| Step 3 (API 재배포) 소요 | _분_ |
| 첫 `/execute` → `status=success` 소요 | _초_ (cold start 포함) |
| 2, 3번째 `/execute` 평균 소요 | _초_ (warm pickup) |
| WakeWorker 로그 `woken` 출현 횟수 | _n_ (목표: 1) |
| Worker Cloud Logging 인스턴스 기동 로그 | 있음/없음 |

### 상세 FAIL (해결됨)
_live E2E 중 발견한 실패와 해결 경로를 기입. PLAN_21 §8 리스크 표의 예측이 실제로 재현됐는지 확인._

---

## 3. 리팩토링 (REFACTOR)

- 리팩토링 없음 (조기 추상화 방지) — 해당 없음
- 또는 발견한 중복/개선 항목 기록

---

## 4. 보안 감사 (SECURITY_AUDITOR)

| 규칙 | 결과 |
|------|------|
| I01 tfvars 실값 커밋 | PASS |
| I02 tfstate 커밋 | PASS |
| I03 HCL 시크릿 하드코딩 | PASS |
| I04 프로젝트 ID 하드코딩 | PASS |
| I05 gcloud stdout 유출 | PASS (러너는 `--format='value(timestamp)'` 로 타임스탬프만) |
| I06 GH Actions secret 로그 | N/A |
| I07 deletion_protection / ignore_changes | PASS |
| I08 Ruleset bypass (WARN) | _기록_ |
| I09 .gitignore 필수 항목 | PASS |
| I10 IAM 최소권한 (WARN) | roles/run.developer → 후속 작업: 커스텀 role `workerPool.updateOnly` 축소 (PLAN_21 §8) |

---

## 5. 사후 영향 평가 (IMPACT_ASSESSOR)

- **리스크 등급**: 🟡 MEDIUM
- **근거**: MANUAL 모드 scale-down 이 자동 아님 → 미구현 상태에서 staging 을 방치하면 Worker Pool 인스턴스 1 대가 계속 과금
- **terraform plan**: _add=N change=N destroy=N (Step 2 후 기입)_
- **다운스트림 영향**:
  | 브랜치 | 영향 | 후속 PR |
  |--------|------|---------|
  | API_Server | ✅ inline 분기 제거 대기 (Phase 6.2) | _TBD_ |
  | Database | ➖ | 없음 |
  | Execution_Engine | ➖ | 없음 |
- **롤백 계획**:
  - [ ] staging 선 검증 완료
  - [ ] Memorystore/Worker Pool destroy 가능 (prevent_destroy 해제 경로 확인)
  - [ ] tfstate 로컬 스냅샷 경로: `infra/terraform/terraform.tfstate.backup.<ts>`

---

## 6. 리뷰 (REVIEW)

- Critical: 0 건
- Major: 1 건 — MANUAL 모드 scale-down 자동화 부재 (후속 작업으로 등록)
- Minor: _TBD_

잔존 항목:
- [Major] `worker.tf` scaling — idle watchdog 미구현 — Cloud Scheduler + Cloud Functions 로 분리 추적 (post-Phase-6 ticket)

---

## 7. 사용자 승인 기록

- staging apply 승인: _YES/NO (타임스탬프)_
- staging destroy 승인: _YES/NO_
- 특이사항: MANUAL scale-down 은 destroy 로 대체됨을 승인

---

## 8. 다음 Phase 권고사항

1. **Phase 6.2** — API_Server inline 분기 + `test_execute_inline.py` 제거, `.github/workflows/inline-guard.yml` 신설 (infra 브랜치). Live E2E 가 성공한 후에만 진행.
2. **Scale-down watchdog** — Cloud Scheduler (5분 간격) → Cloud Functions → `workerPools.patch(manual_instance_count=0)` (단, Celery 큐 empty 확인 후). ADR-021 Update 섹션에 기입.
3. **IAM 축소** — `roles/run.developer` → 커스텀 role `run.workerPools.update` 한 권한만. live 로그로 실제 필요 권한 셋 확정 후.
4. **ADR-021 Phase 표 갱신** — docs 브랜치 별도 PR 로 ✅ 마킹.
5. **실측 비용 기록** — Memorystore BASIC 1GB / Worker Pool 0.5 vCPU×1h / API min=1 각 카테고리별 일일 코스트 (staging 세션 기준) 를 `docs/context/decisions.md` ADR-021 Consequences 에 추가.
