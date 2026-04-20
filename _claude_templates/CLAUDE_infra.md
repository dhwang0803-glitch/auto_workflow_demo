# infra — Claude Code 브랜치 지침

> 루트 `CLAUDE.md` 보안 규칙과 함께 적용된다.

## 모듈 역할

**Infrastructure / Deployment / DevOps** — GCP 리소스 프로비저닝 (Terraform),
배포·마이그레이션 bash 스크립트, 운영 runbook.

크로스 모듈 운영 작업의 단일 소유자:
- `API_Server` · `Database` · `Execution_Engine` 가 공유하는 Cloud Run / Cloud SQL / Secret Manager / VPC 를 이 브랜치에서 프로비저닝
- `.github/workflows/**` (CI/CD) 는 물리적으로는 루트에 있지만 **infra 브랜치가 소유**한다 (GitHub 요구 경로라 이동 불가)
- 모듈 1개에만 속한 operational 파일 (예: `API_Server/Dockerfile`) 은 해당 모듈 브랜치에 남긴다 (memory 예외 규칙)

---

## 관련 문서

| 문서 | 내용 |
|------|------|
| `infra/docs/README.md` | main → development → release 3단 배포 절차 + 시크릿 R/W 패턴 |
| `infra/docs/README_oauth.md` | Google OAuth client 등록 및 시크릿 주입 절차 |
| `docs/context/decisions.md` — ADR-018 | Cloud SQL + Secret Manager 도입 결정 |
| `docs/context/decisions.md` — ADR-019 | Google OAuth2 / Workspace 노드 통합 |
| `docs/context/decisions.md` — ADR-020 | tfstate remote backend 부재, public IP 정책 |
| `docs/context/decisions.md` — ADR-021 | (pending) Worker 배포 경로 — Cloud Run Worker Pools vs Cloud Tasks vs GKE |

ADR 본체는 `docs` 브랜치에서만 수정. infra 브랜치 PR 은 링크만.

---

## 파일 위치 규칙 (MANDATORY)

```
infra/
├── terraform/                   ← *.tf, modules/, environments/ (Terraform 관례)
│   ├── main.tf
│   ├── cloud_run.tf
│   ├── network.tf
│   ├── variables.tf
│   ├── outputs.tf
│   ├── versions.tf
│   └── environments/
│       ├── staging.tfvars.example
│       └── prod.tfvars.example
├── scripts/                     ← 배포·마이그레이션 bash
│   ├── migrate_via_proxy.sh
│   ├── inject_oauth_secrets.sh
│   └── run_e2e_workspace_node.sh
├── docs/                        ← runbook
│   ├── README.md                ← main → development → release 3단 배포 절차
│   └── README_oauth.md          ← Google OAuth secret 주입 절차
├── agents/                      ← infra TDD 사이클 9-에이전트 지시사항
│                                  (ORCHESTRATOR / TEST_WRITER / DEVELOPER / TESTER /
│                                   REFACTOR / REVIEW / SECURITY_AUDITOR /
│                                   IMPACT_ASSESSOR / REPORTER)
├── plans/                       ← PLAN_NN_*.md (ADR Phase 와 1:1 매핑)
├── reports/                     ← TDD 사이클 결과 보고서 (REPORTER 출력)
├── tests/                       ← bats + terraform validate/plan + tflint/checkov
└── config/                      ← (필요 시) ops-level 설정
```

**infra 브랜치가 소유하되 루트 경로에 유지**:
- `.github/workflows/*.yml` — GitHub 강제
- `.dockerignore` — docker build 컨텍스트 루트
- `_claude_templates/CLAUDE_infra.md` — 템플릿 허브 자체

---

## 배포 흐름 (브랜치 모델)

```
main  ──(PR 머지)──▶  main
                        │
                        ├─ ff-only push ▶  development  ──▶ staging Cloud Run (GH Actions staging-deploy)
                        │
                        └─ ff-only push ▶  release      ──▶ prod Cloud Run    (GH Actions release-deploy)
```

- `main` 은 개발 기본. 기능 PR 은 `main` 을 base 로.
- `development` / `release` 는 **배포 트리거용 포인터 브랜치**. 개발 금지. fast-forward push 만 허용.
- ruleset: `development`, `release` 는 `deletion` + `non_fast_forward` 규칙만 유지 (pull_request 규칙 제거됨 — ff-only push 를 위해 의도적으로 제거).
- staging apply 는 PR 머지 → development push 로, prod apply 는 release push 로 트리거.

---

## 환경 구분 규칙 (MANDATORY)

- Terraform 명령은 **항상 `-var-file=environments/<env>.tfvars` 를 명시**한다. 기본값 사용 금지 (prod 오적용 방지).
- `<env>` ∈ `{staging, prod}`. 다른 이름 금지.
- 모든 리소스 이름에 `${var.environment}` suffix 포함 (`auto-workflow-staging`, `db-password-prod`, …). Terraform 이 이 규약을 강제하진 않으므로 신규 리소스 작성 시 수동 준수.
- staging 과 prod 는 **동일 GCP 프로젝트** (`autoworkflowdemo`) 를 공유한다 (memory: `project_gcp_project_strategy`). 프로젝트 분리 기반의 IAM 분리는 불가 — 리소스 이름 기반으로 격리.

---

## 기술 스택

```hcl
# Terraform
terraform { required_version = ">= 1.6" }
provider "google" { ... }
resource "google_cloud_run_v2_service" "api" { ... }
resource "google_sql_database_instance" "pg" { ... }
```

```bash
# scripts — gcloud + terraform + cloud-sql-proxy
gcloud secrets versions access latest --secret=...
terraform apply -var-file=environments/staging.tfvars
cloud-sql-proxy <instance-connection-name>
```

---

## 실행

```bash
# Terraform
cd infra/terraform && terraform init
terraform plan  -var-file=environments/staging.tfvars   # 항상 plan 먼저
terraform apply -var-file=environments/staging.tfvars

# 마이그레이션 (Cloud SQL Auth Proxy 경유)
bash infra/scripts/migrate_via_proxy.sh staging

# OAuth 시크릿 주입
bash infra/scripts/inject_oauth_secrets.sh staging /path/to/client_secret.json

# E2E 노드 실행
bash infra/scripts/run_e2e_workspace_node.sh staging <cred_id> gmail_send '{...}'
```

---

## Terraform 적용 규칙 (MANDATORY)

1. **`terraform plan` 없이 apply 금지**. plan 결과를 육안 확인 후 apply.
2. **staging 먼저, 그 다음 prod**. 동일 변경을 prod 에 먼저 적용하지 않는다.
3. **prod 에 `--auto-approve` 금지**. 항상 대화형 prompt 로 확인.
4. **destroy 대상 존재 시 사용자 승인 필수** — 실수로 지울 수 있는 리소스는 plan 출력에서 빨간색으로 드러난다.
5. **`tfstate` 는 로컬에만 존재** (ADR-020). 작업 전/후 `infra/terraform/terraform.tfstate` 백업 권장. remote backend 도입 시 ADR 갱신.
6. **`*.tfvars` 실값 커밋 금지**. `.gitignore` 에 포함. `*.tfvars.example` 로 구조만 공유.

---

## Destroy 프로토콜

prod 리소스 destroy 는 원칙적으로 금지. staging 에서만 실행한다.

- `var.deletion_protection = true` 인 리소스는 Terraform 이 destroy 거부 → **옳은 상태**. 해제하지 말고 prod 리소스는 유지.
- staging destroy 후 재생성 시 serverless-ipv4 VPC peering 해제 → 재할당까지 **최대 45분** (이전 세션에서 확인). 재적용 타이밍 여유 확보 필수.
- Cloud SQL instance 삭제 후 같은 이름 재생성은 **최소 1주** 대기 필요 (GCP 제약). 이름 변경 없이 destroy 하면 재생성 블록.
- Secret Manager secret 은 destroy 시 소프트 삭제 후 30일 보관 — 재생성 시 이전 값 복구 가능하지만 권장 X.

---

## cloud-sql-proxy 컨벤션

- Windows 로컬 개발: `.tmp/cloud-sql-proxy.exe` (repo root 기준). 비-Windows 는 `.tmp/cloud-sql-proxy`.
- `.tmp/` 디렉토리는 `.gitignore`. 바이너리 직접 커밋 금지.
- 스크립트는 `REPO_ROOT/.tmp/cloud-sql-proxy[.exe]` 순서로 탐색 — `infra/scripts/run_e2e_workspace_node.sh` 참조.
- 포트 기본: 로컬 마이그레이션 `5433`, E2E runner `15434` (5432 는 Hyper-V 충돌, 5433 은 마이그레이션 전용 — memory: `reference_gcp_terraform_gotchas`).

---

## Artifact Registry 이미지 태그 컨벤션

```
asia-northeast3-docker.pkg.dev/autoworkflowdemo/auto-workflow/<service>:<tag>
```

- `<service>`: `api` (API_Server), `worker` (Execution_Engine), `agent` (향후).
- `<tag>`: `<feature-slug>-<git-sha7>` (예: `logging-fix-632d8f8`) 권장. `latest` 금지 (rollback 추적 불가).
- 이미지 push 권한은 GH Actions 서비스 계정에만. 로컬 docker push 는 debug 용 임시 태그 (`debug-YYYYMMDD-HHMM`) 로만 허용.
- Cloud Run `cloud_run.tf` 의 `image = "...:tag"` 변경은 **infra PR 로 커밋**. 앱 코드 PR 에서 이미지 태그를 바꾸지 않는다.

---

## 관측 / 로그 조회 패턴

```bash
# Cloud Run 서비스 로그 (지난 1h)
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="api-staging"' \
  --limit=50 --freshness=1h --format='value(timestamp,severity,textPayload)'

# 특정 logger (예: api_server.email)
gcloud logging read \
  'resource.type="cloud_run_revision" AND jsonPayload.logger="api_server.email"' \
  --limit=20 --freshness=1h

# Cloud SQL 쿼리 로그 (pgaudit)
gcloud logging read \
  'resource.type="cloudsql_database" AND resource.labels.database_id~"auto-workflow-staging"' \
  --limit=30 --freshness=30m
```

- 운영 장애 조사 시 **항상 staging 부터** 같은 쿼리로 재현 시도.
- `uvicorn` 은 `uvicorn.*` 로거만 설정 — 앱 로거(`api_server.*`) 가 Cloud Logging 에 보이지 않으면 `logging.basicConfig` 누락 (PR #a633076 회고).

---

## 보안 주의사항 (MANDATORY)

1. **시크릿 R/W 는 stdout 금지**:
   - 쓰기: `echo -n "$value" | gcloud secrets versions add ... --data-file=-`
   - 읽기: `val="$(gcloud secrets versions access ...)"` 로 쉘 변수 캡처, argv/로그 노출 금지
   - 상세: `infra/docs/README.md` "개발자 workstation 위생" 섹션
2. **tfvars 실값 커밋 금지**: `*.tfvars` 는 `.gitignore`, `*.tfvars.example` 만 커밋
3. **tfstate 커밋 금지**: 로컬에만. remote backend 미적용 (ADR-020)
4. **Placeholder 구분**: Fernet/JWT 시크릿은 `REPLACE_ME_…` 로컬 검증용, 실키는 GH Secret + Secret Manager
5. **prod destroy 금지**: `deletion_protection = true` 유지. staging 에서만 destroy.
6. **GH Actions secret 은 env 로만 주입**, `run:` 스텝에서 `echo ${{ secrets.X }}` 금지.

기계적 점검은 `infra/agents/SECURITY_AUDITOR.md` (규칙 I01~I10) 실행기에서 수행.

---

## 인터페이스

- **업스트림**: `API_Server` / `Database` / `Execution_Engine` 브랜치의 Dockerfile · migration SQL (인프라가 빌드/배포)
- **다운스트림**: GCP 리소스 (Cloud Run, Cloud SQL, Secret Manager, Artifact Registry, VPC)

---

## PR 범위 규칙

- Terraform 변경: infra 브랜치 단독 PR
- Dockerfile 변경: **해당 모듈 브랜치** (API_Server / Execution_Engine) 에서. infra 가 아님.
- `.github/workflows/**` 변경: infra 브랜치 PR (루트 경로 유지)
- `docs/context/**` (ADR, MAP, architecture): **docs 브랜치** PR. infra 에서 수정 금지.
- 크로스 브랜치 영향 있는 변경 (예: Terraform 스키마 변경으로 API_Server env 추가): PR 본문 `사후 영향 평가` 섹션에 명시 + 다운스트림 브랜치 PR 을 별도 분리.
