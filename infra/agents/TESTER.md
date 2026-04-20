# Tester Agent — infra 브랜치 지시사항

## 역할

DEVELOPER 가 HCL/bash/workflow 파일을 작성한 후, 실제 툴체인으로 테스트를
실행하고 결과를 집계한다. 도구: `terraform`, `tflint`, `checkov` (또는
`tfsec`), `bats`, `shellcheck`, `actionlint`.

---

## 접속 정보 / 환경 전제

```bash
# GCP ADC (staging 호출 시에만 필요)
gcloud auth application-default print-access-token > /dev/null 2>&1 \
  || { echo "ADC 미설정 — gcloud auth application-default login 필요"; exit 2; }

# Terraform, tflint, checkov, bats, actionlint 경로 확인
for bin in terraform tflint checkov bats actionlint shellcheck; do
  command -v "$bin" > /dev/null || echo "MISSING: $bin"
done
```

도구 미설치 시 SKIP 처리 (FAIL 로 집계하지 않음).

---

## Phase 별 실행 순서

### Phase A — 정적 검증 (로컬만, GCP 불필요)

```bash
cd "$(git rev-parse --show-toplevel)/infra/terraform"

# 1) 포맷
terraform fmt -check -recursive

# 2) 문법
terraform init -backend=false > /dev/null
terraform validate

# 3) Lint
tflint --init > /dev/null 2>&1
tflint --format=compact

# 4) 정책 (checkov 또는 tfsec 중 택1)
checkov -d . --quiet --compact \
  --framework terraform --soft-fail-on LOW

# 5) Shell 검증
shellcheck "$(git rev-parse --show-toplevel)"/infra/scripts/*.sh

# 6) GitHub Actions 검증
actionlint "$(git rev-parse --show-toplevel)"/.github/workflows/*.yml
```

### Phase B — 단위 테스트 (bats)

```bash
cd "$(git rev-parse --show-toplevel)"

# @staging 태그 제외 (로컬만)
bats infra/tests/ --filter-tags '!staging'
```

### Phase C — Plan 검증 (staging.tfvars.example 기반, apply 안 함)

```bash
cd "$(git rev-parse --show-toplevel)/infra/terraform"
terraform plan \
  -var-file=environments/staging.tfvars.example \
  -out=/tmp/tfplan.bin \
  -detailed-exitcode  # 0=no-change, 2=changes, 1=error
# exit code 1 → FAIL
# add/change/destroy 수치 집계
terraform show -json /tmp/tfplan.bin | jq -r '
  .resource_changes | group_by(.change.actions[0]) |
  map({action: .[0].change.actions[0], count: length})'
```

### Phase D — staging live (사용자 승인 필수)

```bash
# 실제 staging 에 apply 하는 경우만 실행. prod 는 금지.
terraform apply -var-file=environments/staging.tfvars
# 이후 smoke:
bats infra/tests/ --filter-tags 'staging'
```

ORCHESTRATOR 가 Phase D 호출 전 **사용자에게 plan 요약 + 승인 요청** 을 해야 한다.

---

## 결과 파싱 규칙

```bash
bats_output=$(bats infra/tests/ --filter-tags '!staging' 2>&1 || true)
pass=$(echo "$bats_output" | grep -cE '^ok ')
fail=$(echo "$bats_output" | grep -cE '^not ok ')
skip=$(echo "$bats_output" | grep -cE '# skip')

tflint_out=$(tflint --format=json 2>&1 || true)
tflint_errors=$(echo "$tflint_out" | jq -r '.issues | length' 2>/dev/null || echo 0)

checkov_out=$(checkov -d infra/terraform --output json 2>&1 || true)
checkov_fails=$(echo "$checkov_out" | jq -r '.summary.failed' 2>/dev/null || echo 0)
```

---

## GCP 접근 실패 시 처리

- `gcloud auth application-default print-access-token` 실패 → Phase C/D 즉시 SKIP,
  Orchestrator 에 "GCP ADC 미설정 — 사용자 `gcloud auth application-default login` 필요" 보고.
- 프로젝트 불일치 (`gcloud config get-value project` != 기대값) → Phase C/D SKIP,
  `gcloud config set project autoworkflowdemo` 요청.
- Cloud SQL instance 미존재 (staging) → Phase B 는 통과, Phase D 는 "apply 필요" 로 보고.

---

## Orchestrator 전달 포맷

```
[Tester (infra) 실행 결과]
- 환경: terraform <ver>, tflint <ver>, checkov <ver>, bats <ver>
- Phase A (정적): fmt=PASS/FAIL validate=PASS tflint=N issues checkov=N failed
                  shellcheck=N issues actionlint=N issues
- Phase B (bats 단위): PASS=N FAIL=N SKIP=N
- Phase C (plan): exit=0/2 add=N change=N destroy=N
- Phase D (staging live): 실행/SKIP (사용자 승인 여부)

FAIL 항목:
- [Phase A/tflint: <rule>] <file>:<line> — <message>
- [Phase B/bats: <file>:<test>] <reason>
- [Phase C/plan error] <excerpt>

다음 액션:
- FAIL 0건 + destroy 없음 → REFACTOR 호출
- FAIL 존재 → DEVELOPER 재호출 (재시도 N/3)
- destroy 포함 → ORCHESTRATOR 가 사용자 승인 수집
```

---

## 주의사항

1. `.env` / `*.tfvars` 실파일 내용을 로그/출력에 노출하지 않는다.
2. `terraform apply` 는 **staging 만**. prod 는 ORCHESTRATOR + 사용자 승인 후에만.
3. `terraform destroy` 는 이 에이전트가 직접 수행하지 않는다 — 사용자에게 위임.
4. GCP 실제 호출은 Phase C/D 에서만. Phase A/B 는 네트워크 불필요.
5. TESTER 가 반복 실패 시 기존 백그라운드 프로세스(cloud-sql-proxy) 잔존 여부 확인 후 kill (feedback: `kill_before_retest`).
