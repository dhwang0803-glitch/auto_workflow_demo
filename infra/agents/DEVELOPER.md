# Developer Agent — infra 브랜치 지시사항

## 역할

TEST_WRITER 가 작성한 실패 테스트를 통과하는 **최소한의 Terraform/bash/
workflow 변경**을 구현한다 (TDD Green). 과도한 모듈화·사전 추상화 금지.

---

## 구현 원칙

1. **실패 테스트 통과 최우선**. 현재 실패하는 테스트만 타깃.
2. **최소 구현**. 동작하는 가장 단순한 HCL/bash 작성.
3. **파일 위치 규칙** 엄수 (`infra/CLAUDE.md` "파일 위치 규칙 MANDATORY").
4. **앱 코드 수정 금지**. API_Server/Database/Execution_Engine/Frontend 파일은
   손대지 않는다. 필요 시 IMPACT_ASSESSOR 가 다운스트림 PR 로 위임.
5. **시크릿 값 하드코딩 금지**. 반드시 `var.` 또는 `random_password` 경유.

---

## 파일 위치 & 대상

| 변경 유형 | 위치 | 주의 |
|-----------|------|------|
| 리소스 정의 | `infra/terraform/<topic>.tf` (main/cloud_run/network/...) | 한 파일이 300줄 넘으면 분리 검토 |
| 변수 | `infra/terraform/variables.tf` | 타입/description 필수, 기본값은 안전한 값만 |
| 출력 | `infra/terraform/outputs.tf` | 시크릿은 `sensitive = true` |
| 환경값 | `infra/terraform/environments/<env>.tfvars.example` | 실값은 절대 금지 |
| 배포 스크립트 | `infra/scripts/<name>.sh` | `set -euo pipefail` 필수 |
| 공용 bash 헬퍼 | `infra/scripts/lib/<name>.sh` | sourced, executable 아님 |
| Runbook | `infra/docs/README*.md` | |
| Workflow | `.github/workflows/*.yml` (루트 경로 유지) | infra 소유 |

**루트 또는 `infra/` 직하에 `.tf` / `.sh` 생성 금지.**

---

## Terraform 작성 컨벤션 (MANDATORY)

```hcl
# 1. 리소스 이름은 환경 suffix 포함
resource "google_sql_database_instance" "main" {
  name = "auto-workflow-${var.environment}"
  ...
}

# 2. 플래그는 반드시 var 경유 (prod 오적용 방지)
deletion_protection = var.deletion_protection

# 3. placeholder secret_version 은 ignore_changes 필수
resource "google_secret_manager_secret_version" "foo_placeholder" {
  secret      = google_secret_manager_secret.foo.id
  secret_data = "PLACEHOLDER_UPLOAD_FROM_CONSOLE"
  lifecycle {
    ignore_changes = [secret_data]
  }
}

# 4. 시크릿 값은 랜덤 생성 또는 외부 주입. 리터럴 금지.
#    Bad:  secret_data = "abcd1234..."
#    Good: secret_data = random_password.x.result
#          secret_data = var.x_secret   # tfvars 로 주입
#          secret_data = "PLACEHOLDER..." + ignore_changes

# 5. for_each / locals 는 3회 이상 반복될 때만 도입 (조기 추상화 금지)
```

금지:
- `provider "google" { credentials = file("key.json") }` — ADC 만 사용
- `terraform { backend "gcs" {} }` — ADR-020 이전엔 local backend
- `count = 0/1` 로 환경 분기 — `var.environment` 가 결정하는 리소스는 분기 대신 always-on 하거나 module 로 분리

---

## Bash 스크립트 작성 컨벤션 (MANDATORY)

```bash
#!/usr/bin/env bash
# 용도 한 줄 설명.
#
# Usage:
#   bash infra/scripts/<name>.sh <env> <arg...>

set -euo pipefail

# 1. 인자 검증 먼저
if [ $# -lt 2 ]; then
  echo "usage: $0 <env: staging|prod> <arg>" >&2
  exit 2
fi
ENV_NAME="$1"; ARG="$2"
case "$ENV_NAME" in staging|prod) ;; *) echo "bad env" >&2; exit 2 ;; esac

# 2. REPO_ROOT 계산 (infra/scripts 기준 2단계 위)
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

# 3. 시크릿은 반드시 변수 캡처 (stdout 금지) — SECURITY I05
VAL="$(gcloud secrets versions access latest --secret="foo-${ENV_NAME}")"

# 4. 시크릿 주입은 stdin pipe — SECURITY I05
echo -n "$NEW_VAL" | gcloud secrets versions add "foo-${ENV_NAME}" --data-file=-

# 5. 백그라운드 프로세스는 trap 으로 정리
PROXY_LOG="$(mktemp)"
"$PROXY" --port="$PORT" "$INSTANCE" > "$PROXY_LOG" 2>&1 &
PID=$!
cleanup() { kill "$PID" 2>/dev/null || true; rm -f "$PROXY_LOG"; }
trap cleanup EXIT INT TERM

# 6. 종료 전 민감 변수 unset
unset VAL
```

금지:
- `eval "$input"` / `bash -c "$user_input"`
- `curl ... | bash`
- `echo "$SECRET"` — 디버그 용도라도 금지
- Windows 경로 하드코딩 (`/c/Users/...`) — `PYBIN` 같은 env 로 빼기

---

## GitHub Actions 작성 컨벤션

```yaml
- name: Deploy
  env:
    DEPLOY_TOKEN: ${{ secrets.DEPLOY_TOKEN }}  # env 로만 주입
  run: |
    # run: 블록에서 ${{ secrets.* }} 직접 사용 금지 — 로그 유출 위험
    curl -H "Authorization: Bearer $DEPLOY_TOKEN" ...
```

- `permissions:` 블록 명시 (principle of least privilege)
- `concurrency:` 로 중복 실행 방지 (staging-deploy / release-deploy 모두)
- `uses: actions/...@<sha>` — 버전 태그 대신 SHA pin 권장 (보안)

---

## 구현 완료 후 자가 점검

- [ ] 하드코딩된 시크릿/프로젝트 ID/인스턴스명 없음
- [ ] `deletion_protection`, `public_ip_enabled` 등 민감 플래그는 `var.` 경유
- [ ] placeholder secret 리소스에 `lifecycle.ignore_changes` 존재
- [ ] bash 스크립트 `set -euo pipefail` + `trap cleanup`
- [ ] gcloud 시크릿 R 은 `$(...)` 캡처, W 는 `--data-file=-`
- [ ] `.github/workflows` 변경 시 secret 은 env 주입 (run 블록 echo 금지)
- [ ] `terraform fmt` 실행 완료
- [ ] 한 PR 에 한 개 토픽 (여러 리소스 무관한 변경 섞지 않음)
