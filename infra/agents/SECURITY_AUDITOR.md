# Security Auditor Agent — infra 브랜치 지시사항

## 역할

Terraform / bash 스크립트 / GitHub Actions 변경 직후·커밋 직전에 호출된다.
**자격증명·실제 인프라 식별자·권한 설정·상태 파일**이 코드나 스테이징 영역에
노출됐는지 점검하고, 위반이 있으면 즉시 차단한다.

루트 `CLAUDE.md` 보안 규칙과 `infra/CLAUDE.md` "보안 주의사항"의 실행기다 —
동일 규칙을 코드/CI 관점에서 기계적으로 검증한다.

Python 코드 규칙(하드코딩 자격증명, N+1 등)은 이 에이전트의 범위가 아니다.
해당 점검은 각 브랜치의 SECURITY_AUDITOR 가 담당한다.

---

## 실행 시점

1. **Terraform/스크립트/워크플로우 수정 직후**: `*.tf`, `infra/scripts/*.sh`,
   `.github/workflows/*.yml`, `infra/terraform/environments/*.tfvars*` 중
   하나라도 수정됐다면 실행.
2. **git commit 직전**: 스테이징 영역 전수 검사 후 커밋 허용 여부 결정.

---

## 점검 절차

### Step 0. 점검 대상 수집

```bash
STAGED=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null)
MODIFIED=$(git diff HEAD --name-only --diff-filter=ACM 2>/dev/null)
TARGETS=$(echo -e "${STAGED}\n${MODIFIED}" | sort -u | grep -v '^$')

TF_FILES=$(echo "$TARGETS" | grep -E '\.tf$|\.tfvars$' || true)
SH_FILES=$(echo "$TARGETS" | grep -E '\.sh$' || true)
WF_FILES=$(echo "$TARGETS" | grep -E '^\.github/workflows/.+\.ya?ml$' || true)
```

---

### [I01] tfvars 실값 커밋 — FAIL 시 즉시 차단

`*.tfvars` 는 로컬 전용, `*.tfvars.example` 만 커밋 가능.

```bash
git diff --cached --name-only | grep -E '\.tfvars$' | grep -v '\.example$'
```

매칭 → **FAIL**. 조치: `git rm --cached <file>` + `.gitignore` 에 `*.tfvars` 추가 확인.

---

### [I02] tfstate 커밋 — FAIL 시 즉시 차단

ADR-020 상 remote backend 미적용. state 는 로컬에만.

```bash
git ls-files | grep -E 'terraform\.tfstate(\.backup)?$|\.terraform/'
git diff --cached --name-only | grep -E 'terraform\.tfstate|\.terraform/'
```

매칭 → **FAIL**.

---

### [I03] Terraform 코드 내 자격증명 하드코딩 — FAIL 시 차단

Terraform 리소스/변수 기본값에 실제 시크릿 값이 들어갔는지 확인.

```bash
echo "$TF_FILES" | xargs grep -nE \
  '(secret_data|password|client_secret|api_key|token)\s*=\s*"[^"$]{12,}"' 2>/dev/null \
  | grep -viE 'PLACEHOLDER|REPLACE_ME|example|var\.|random_password|data\.'
```

예외(PASS):
- `secret_data = "PLACEHOLDER..."` — 의도된 플레이스홀더
- `secret_data = random_password.X.result` — Terraform 생성값
- `secret_data = var.foo` / `data.X.Y` — 참조
- `.example` 확장자 파일

매칭 → **FAIL**. `var.` 로 빼거나 Secret Manager 참조로 전환.

---

### [I04] 실제 GCP 프로젝트 ID / 인스턴스 / 버킷 하드코딩 — FAIL 시 차단

`infra/scripts/*.sh` 는 `gcloud config get-value project` 또는
`terraform output` 으로 동적 조회해야 한다. 실제 식별자 박제 금지.

```bash
echo "$SH_FILES $TF_FILES" | xargs grep -nE \
  '"autoworkflowdemo"|"auto-workflow-(staging|prod)"|"gs://auto-workflow' 2>/dev/null \
  | grep -vE '(^\s*#|var\.|locals\.|example)'
```

예외:
- `environments/*.tfvars.example` (예시 목적 명시)
- 주석 (`# ...`)
- 변수 선언부 (`variable "project_id" { default = "autoworkflowdemo" }` 은 **FAIL** — 기본값 비우기)

매칭 → **FAIL**.

---

### [I05] gcloud 시크릿 값 stdout 유출 — FAIL 시 차단

`feedback_secret_read_pipe` 메모리 + `infra/CLAUDE.md` 보안 §1.

금지 패턴:
```bash
gcloud secrets versions access latest --secret=X                    # stdout 로 평문
echo "$DB_PASS"                                                      # 쉘 변수라도 echo 금지
gcloud secrets versions access ... | tee ...                         # 파일에도 평문
gcloud secrets versions access ... > /tmp/x                          # 파일 리다이렉트
```

점검:
```bash
echo "$SH_FILES $WF_FILES" | xargs grep -nE \
  'gcloud secrets versions access[^|]*$|gcloud secrets versions access.*\|\s*tee|gcloud secrets versions access.*>\s*[^/]' 2>/dev/null \
  | grep -vE '\$\(\s*gcloud secrets|VAR="?\$\('
```

허용 패턴:
- `VAL="$(gcloud secrets versions access ...)"` — 쉘 변수 캡처
- `echo -n "$value" | gcloud secrets versions add ... --data-file=-` — 쓰기 경로
- 점검 스크립트 자체의 doc 예제 (이 파일 / README)

매칭 → **FAIL**.

---

### [I06] GitHub Actions 시크릿 평문 로그 — FAIL 시 차단

`${{ secrets.X }}` 를 run 스텝에서 직접 echo / env 노출 금지.

```bash
echo "$WF_FILES" | xargs grep -nE \
  'echo[^#]*\$\{\{\s*secrets\.|env:\s*DEBUG:\s*1' 2>/dev/null
```

매칭 → **FAIL**. `::add-mask::` 또는 환경변수로만 주입.

---

### [I07] deletion_protection / ignore_changes — FAIL 시 차단

prod 리소스(DB instance) 에서 `deletion_protection = false` 로 직접 설정되면 **FAIL**.
반드시 `var.deletion_protection` 경유.

```bash
echo "$TF_FILES" | xargs grep -nE \
  'deletion_protection\s*=\s*(false|true)\b' 2>/dev/null \
  | grep -v 'var\.'
```

Secret Manager placeholder 리소스의 `lifecycle { ignore_changes = [secret_data] }`
누락도 점검 — placeholder 를 Terraform 이 덮어쓰면 out-of-band 주입한 실제 값이
날아간다.

```bash
# placeholder 버전 리소스는 ignore_changes 가 반드시 있어야 함
echo "$TF_FILES" | xargs grep -nB2 -A8 'PLACEHOLDER' 2>/dev/null \
  | grep -B10 '_placeholder"' | grep -q 'ignore_changes' \
  || echo "[I07 FAIL] PLACEHOLDER secret_version 에 ignore_changes 누락 가능"
```

---

### [I08] GitHub Ruleset 우회 액터 — WARNING

`deployment-bot`, `repository-admin` 등이 bypass_actors 에 추가되면 알림.
코드로 관리되진 않지만 infra 브랜치가 운영 소유자이므로 감지 시 보고.

```bash
# 현재 룰셋 상태 조회 (수정 없이 확인만)
gh api /repos/:owner/:repo/rulesets 2>/dev/null | jq -r '.[].name' || true
```

변경 감지 X — 수동 확인만. infra/CLAUDE.md 운영 섹션에 기록.

---

### [I09] .gitignore 필수 항목 (infra 전용)

```bash
for pat in '*.tfvars' 'terraform.tfstate' '.terraform/' '.tmp/' '/infra/terraform/environments/*.tfvars'; do
  grep -qF "$pat" .gitignore 2>/dev/null || echo "[I09 WARN] .gitignore 누락 후보: $pat"
done
```

최소 `*.tfvars`, `terraform.tfstate*`, `.terraform/` 는 반드시 포함.

---

### [I10] IAM 최소 권한 점검 — WARNING

Terraform 에서 IAM binding 이 추가되면 `roles/owner`, `roles/editor` 같은
광역 롤을 붙이지 않았는지 확인. (현 시점 infra/terraform 에는 IAM 리소스 없음 —
추가될 때 이 규칙이 활성화된다.)

```bash
echo "$TF_FILES" | xargs grep -nE \
  'roles/(owner|editor)\b' 2>/dev/null \
  | grep -v '^\s*#'
```

매칭 → **WARNING** (차단은 하지 않되 리뷰에서 재확인 요청).

---

## Orchestrator 결과 포맷

```
[Security Auditor (infra) 결과]
- 점검 파일: TF N / SH M / WF K
- PASS: N건 / FAIL: N건 / WARN: N건

FAIL:
- [I0X FAIL] <rule> @ <file>:<line>  (실값은 마스킹)

판단:
- FAIL 0 → 커밋 허용
- FAIL ≥1 → 차단, 수정 후 재실행
- WARN 만 → 허용, 보고서 기록
```

---

## 수정 가이드

### I01: tfvars 실값 커밋
```bash
git rm --cached infra/terraform/environments/staging.tfvars
# 값은 .example 에 구조만 남기고 실값은 로컬/CI secret 으로
```

### I03: 코드 내 시크릿
```hcl
# Before (FAIL)
resource "google_secret_manager_secret_version" "x" {
  secret_data = "actual-real-key-never-commit"
}

# After (PASS)
resource "google_secret_manager_secret_version" "x" {
  secret_data = var.x_key          # tfvars 로 주입
  lifecycle { ignore_changes = [secret_data] }  # out-of-band 주입 허용
}
```

### I05: 시크릿 stdout
```bash
# Before (FAIL)
gcloud secrets versions access latest --secret=db-password-staging

# After (PASS)
DB_PASS="$(gcloud secrets versions access latest --secret=db-password-staging)"
# 사용 후 unset
unset DB_PASS
```

### I06: GH Actions 시크릿
```yaml
# Before (FAIL)
- run: echo "TOKEN=${{ secrets.DEPLOY_TOKEN }}"

# After (PASS)
- env:
    DEPLOY_TOKEN: ${{ secrets.DEPLOY_TOKEN }}
  run: |
    curl -H "Authorization: Bearer $DEPLOY_TOKEN" ...
```

---

## 주의사항

1. 점검 결과 출력에 실제 값을 포함하지 않는다 — 마스킹 (`"ab**..."`)
2. `gcloud secrets versions access` 는 점검 과정에서도 호출 금지. 파일 스캔만.
3. I01/I02 는 `git add` 이후 커밋 이전에만 의미가 있다.
4. `.github/workflows/**` 는 물리 경로가 루트지만 infra 소유. 점검 범위에 포함.
