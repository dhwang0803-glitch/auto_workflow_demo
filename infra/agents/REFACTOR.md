# Refactor Agent — infra 브랜치 지시사항

## 역할

모든 테스트가 PASS 된 이후에만 실행된다. Terraform/bash 의 가독성·중복을
개선하되 **plan diff 0** (행동 결과 불변) 를 유지한다 (TDD Refactor).

---

## 핵심 원칙

1. **테스트 통과 상태 유지**: 리팩토링 후 TESTER 재실행 → PASS + `terraform plan`
   **no-change** 확인. 리소스 이름/속성 변경은 리팩토링 아님.
2. **기능 변경 금지**: `terraform plan` 에 add/change/destroy 0건.
3. **조기 추상화 금지**: 동일 패턴 3회 미만 반복은 그대로 둔다 (memory:
   `feedback_avoid_function_sprawl`).
4. **작은 단위로 개선**: 리팩토링 1건 → TESTER → 다음 건.

---

## 개선 검토 항목

### Terraform

- [ ] 동일 문자열(프로젝트 ID, region) 3회 이상 반복 → `locals { }` 추출
- [ ] 유사 리소스 3개 이상 (oauth client_id/secret/redirect_uri 처럼) → `for_each`
- [ ] 동일 블록 구조가 여러 파일에 반복 → `modules/` 추출 검토 (단, 처음부터
      module 만들지 말고 반복이 증명된 후에)
- [ ] `variables.tf` 에 `description` 누락된 변수
- [ ] `outputs.tf` 에서 시크릿인데 `sensitive = true` 누락
- [ ] 긴 주석이 같은 내용을 여러 리소스에 반복 → 상위 section 주석으로 이동

### Bash scripts

- [ ] 3개 이상 스크립트에서 동일 블록 (proxy 기동/종료, 시크릿 로딩) →
      `scripts/lib/<name>.sh` 로 sourced 함수 추출
- [ ] 동일 `gcloud secrets versions access` 호출 패턴 반복 → 헬퍼 함수
- [ ] magic number (포트, timeout) → 상수로 빼기 (스크립트 상단)
- [ ] `echo` / `printf` 메시지 스타일 일관성 (stderr vs stdout)

### GitHub Actions

- [ ] 2개 이상 workflow 에서 동일 setup 스텝 → composite action (`.github/actions/`)
- [ ] 동일 job name / env 중복 → matrix 활용 검토

---

## 리팩토링 범위 제한

제외:
- 테스트 파일 (`infra/tests/`) — 테스트 리팩토링은 TEST_WRITER 역할
- PLAN 문서 (`infra/plans/`)
- `.tfvars*`, `.env*`
- `docs/context/**` (docs 브랜치)

---

## Plan-diff 0 확인

```bash
cd infra/terraform
terraform plan -var-file=environments/staging.tfvars.example \
  -detailed-exitcode -out=/tmp/refactor.plan
# exit code 0 = no change (OK)
# exit code 2 = changes detected (NG — 리팩토링이 동작을 바꿨음, 되돌리기)
```

module 추출 시 resource address 가 바뀌면 `terraform state mv` 로 이동해야
plan-diff 0 유지. state 조작은 사용자 승인 필수.

---

## REPORTER 전달 형식

```
[리팩토링 항목]
- 파일: <file>
- 변경 유형: locals 추출 / for_each 통합 / lib 분리 / sensitive 추가 / 기타
- 변경 전: <기존 구조 1-2줄 요약>
- 변경 후: <개선 구조 1-2줄 요약>
- 개선 이유: <가독성 / 중복 제거 / 일관성>
- plan-diff: no-change (확인됨)
```

---

## 주의사항

- state 주소 변경이 필요한 module 추출은 리팩토링 1회에 1건만. 여러 리소스를
  한 번에 옮기다 실수하면 리소스 destroy/recreate 로 이어진다.
- `terraform fmt -recursive` 는 기본으로 실행하되 커밋 전 diff 검토.
- bash lib 분리 시 sourced 함수는 `return` 이 아닌 `exit` 을 쓰지 않도록 주의.
