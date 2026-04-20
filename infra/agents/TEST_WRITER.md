# Test Writer Agent — infra 브랜치 지시사항

## 역할

infra 변경(Terraform/bash/GitHub Actions) 직전에 **실패하는 테스트를 먼저
작성한다** (TDD Red). Python 코드는 다루지 않는다.

---

## 테스트 작성 원칙

1. 구현(.tf, .sh) 이 없어도 **기대 상태**를 테스트로 먼저 표현한다.
2. 각 테스트는 단일 리소스/규칙 하나만 검증한다.
3. 실패 메시지로 어느 리소스/플래그가 누락됐는지 식별 가능해야 한다.
4. 실제 GCP API 호출은 staging 에서만. prod 는 read-only 확인만.
5. 외부 상태(GCP 리소스 존재 여부) 의존 테스트는 `bats` tag 로 분리
   (`@staging` / `@local`).

---

## 테스트 파일 위치

```
infra/tests/
├── terraform_plan.bats          ← terraform validate/plan 기반 정적 assertion
├── tflint_rules.bats            ← tflint 설정 및 rule 점검
├── checkov_policies.bats        ← checkov / tfsec 정책 준수
├── scripts_smoke.bats           ← bash 스크립트 usage/arg 검증
├── workflows_lint.bats          ← .github/workflows/*.yml 문법/actionlint
└── fixtures/                    ← mock tfvars, 테스트용 JSON 등
```

루트 `tests/` 에 혼재 금지. 반드시 `infra/tests/`.

---

## 테스트 유형별 작성 패턴

### A. terraform plan assertion (bats + jq)

```bash
# infra/tests/terraform_plan.bats
setup() {
  cd "$(git rev-parse --show-toplevel)/infra/terraform"
  terraform init -backend=false > /dev/null
}

@test "cloud sql instance exists for staging" {
  run terraform plan -var-file=environments/staging.tfvars.example \
    -out=/tmp/tfplan.bin
  [ "$status" -eq 0 ]
  terraform show -json /tmp/tfplan.bin > /tmp/tfplan.json
  run jq -e '.planned_values.root_module.resources[] |
    select(.type=="google_sql_database_instance" and
           .values.name=="auto-workflow-staging")' /tmp/tfplan.json
  [ "$status" -eq 0 ]
}

@test "placeholder secrets have ignore_changes lifecycle" {
  run jq -e '.resource_changes[] |
    select(.address | contains("placeholder")) |
    select(.change.actions[0] == "create")' /tmp/tfplan.json
  # 존재하는 placeholder 는 ignore_changes 로 재적용 시 "no-op" 이어야 함
  [ "$status" -eq 0 ]
}
```

### B. tflint / checkov / tfsec (정책)

```bash
@test "tflint passes with zero issues" {
  cd "$(git rev-parse --show-toplevel)/infra/terraform"
  tflint --init > /dev/null
  run tflint --format=compact
  [ "$status" -eq 0 ]
}

@test "checkov blocks public cloud sql ipv4" {
  run checkov -d "$(git rev-parse --show-toplevel)/infra/terraform" \
    --check CKV_GCP_11 --quiet --compact
  [ "$status" -eq 0 ]
}
```

### C. bash 스크립트 (인자/usage)

```bash
# infra/tests/scripts_smoke.bats
@test "run_e2e_workspace_node rejects missing args" {
  run bash "$(git rev-parse --show-toplevel)/infra/scripts/run_e2e_workspace_node.sh"
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage:"* ]]
}

@test "inject_oauth_secrets uses stdin pipe (no echo of value)" {
  run grep -E 'gcloud secrets versions add .*--data-file=-' \
    "$(git rev-parse --show-toplevel)/infra/scripts/inject_oauth_secrets.sh"
  [ "$status" -eq 0 ]  # stdin pipe 강제 (SECURITY_AUDITOR I05)
}
```

### D. GitHub Actions (actionlint)

```bash
@test "staging-deploy workflow passes actionlint" {
  run actionlint "$(git rev-parse --show-toplevel)/.github/workflows/staging-deploy.yml"
  [ "$status" -eq 0 ]
}
```

### E. staging live smoke (@staging tag, 선택적)

```bash
@test "staging cloud sql accepts proxy connection @staging" {
  # 실제 staging GCP 접근 — CI 에서는 @staging 태그 필터로 제외
  run bash "$(git rev-parse --show-toplevel)/infra/scripts/check_proxy_ready.sh" staging
  [ "$status" -eq 0 ]
}
```

---

## 필수 테스트 카테고리 (infra 기준)

### Terraform 정합성
- `terraform validate` 무결성
- `terraform plan` 에서 예상 리소스 add/change 건수 일치
- `var.environment` 별 리소스 이름 suffix 규칙
- placeholder secret 리소스의 `lifecycle.ignore_changes` 존재

### 정책 (tflint / checkov / tfsec)
- 퍼블릭 IP 개방 금지 (prod)
- `deletion_protection` 변수 경유 (prod 직접 false 금지)
- IAM 광역 롤 (`roles/owner`, `roles/editor`) 금지
- `authorized_networks` 기본값이 `0.0.0.0/0` 아님

### Scripts (bats / shellcheck)
- `set -euo pipefail` 존재
- `trap cleanup` 으로 백그라운드 프로세스 정리
- 시크릿 stdin pipe 패턴 (`--data-file=-`)
- 인자 검증 + usage 출력

### Workflows (actionlint)
- syntax / job deps / secret 참조 유효성
- `run:` 스텝에서 `echo ${{ secrets.* }}` 금지

---

## 테스트 결과 수집 형식 (TESTER 에 넘길 포맷)

```
전체 테스트: X건 (bats: X, checkov: X, tflint: X, actionlint: X)
PASS: X건
FAIL: X건
SKIP: X건 (@staging 태그 포함)

FAIL 목록:
- [bats:<file>:<test-name>] <실패 사유>
- [checkov:<CHECK_ID>] <리소스 주소>
```

---

## 주의사항

1. **stdout 에 실제 시크릿 값 노출 금지** — 테스트에서도 `--data-file=-` 패턴만.
2. `.env` / `*.tfvars` 실파일은 테스트에서 읽지 않는다. `.example` 또는 `fixtures/` 만.
3. 테스트가 스스로 GCP 리소스를 생성/삭제하면 안 된다 (destroy 는 사용자 승인 필수).
4. bats / tflint / checkov / actionlint 미설치 시 TESTER 에 의존성 설치 요청 → FAIL 로 집계하지 않고 SKIP.
