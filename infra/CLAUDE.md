# infra — Claude Code 브랜치 지침

> 루트 `CLAUDE.md` 보안 규칙과 함께 적용된다.

## 모듈 역할

**Infrastructure / Deployment / DevOps** — GCP 리소스 프로비저닝 (Terraform), 배포·마이그레이션 bash 스크립트, 운영 runbook.

크로스 모듈 운영 작업의 단일 소유자:
- `API_Server` · `Database` · `Execution_Engine` 가 공유하는 Cloud Run / Cloud SQL / Secret Manager / VPC 를 이 브랜치에서 프로비저닝
- `.github/workflows/**` (CI/CD) 는 물리적으로는 루트에 있지만 **infra 브랜치가 소유**한다 (GitHub 요구 경로라 이동 불가)
- 모듈 1개에만 속한 operational 파일 (예: `API_Server/Dockerfile`) 은 해당 모듈 브랜치에 남긴다 (memory 예외 규칙)

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
├── tests/                       ← (추후) terraform validate / policy check
└── config/                      ← (필요 시) ops-level 설정
```

**infra 브랜치가 소유하되 루트 경로에 유지**:
- `.github/workflows/*.yml` — GitHub 강제
- `.dockerignore` — docker build 컨텍스트 루트
- `_claude_templates/CLAUDE_infra.md` — 템플릿 허브 자체

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

## 실행

```bash
# Terraform
cd infra/terraform && terraform init
terraform apply -var-file=environments/staging.tfvars

# 마이그레이션 (Cloud SQL Auth Proxy 경유)
bash infra/scripts/migrate_via_proxy.sh staging

# OAuth 시크릿 주입
bash infra/scripts/inject_oauth_secrets.sh staging /path/to/client_secret.json

# E2E 노드 실행
bash infra/scripts/run_e2e_workspace_node.sh staging <cred_id> gmail_send '{...}'
```

## 보안 주의사항 (MANDATORY)

1. **시크릿 R/W 는 stdout 금지**:
   - 쓰기: `echo -n "$value" | gcloud secrets versions add ... --data-file=-`
   - 읽기: `val="$(gcloud secrets versions access ...)"` 로 쉘 변수 캡처, argv/로그 노출 금지
   - 상세: `infra/docs/README.md` "개발자 workstation 위생" 섹션
2. **tfvars 실값 커밋 금지**: `*.tfvars` 는 `.gitignore`, `*.tfvars.example` 만 커밋
3. **tfstate 커밋 금지**: 로컬에만. remote backend 미적용 (ADR-020)
4. **Placeholder 구분**: Fernet/JWT 시크릿은 `REPLACE_ME_…` 로컬 검증용, 실키는 GH Secret + Secret Manager

## 인터페이스

- **업스트림**: `API_Server` / `Database` / `Execution_Engine` 브랜치의 Dockerfile · migration SQL (인프라가 빌드/배포)
- **다운스트림**: GCP 리소스 (Cloud Run, Cloud SQL, Secret Manager, Artifact Registry, VPC)

## PR 범위 규칙

- Terraform 변경: infra 브랜치 단독 PR
- Dockerfile 변경: **해당 모듈 브랜치** (API_Server / Execution_Engine) 에서. infra 가 아님.
- `.github/workflows/**` 변경: infra 브랜치 PR (루트 경로 유지)
- 크로스 브랜치 영향 있는 변경 (예: Terraform 스키마 변경으로 API_Server env 추가): `사후 영향 평가` 섹션에 명시
