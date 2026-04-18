# Database Deploy — GCP Cloud SQL

> ADR-018 구현. Terraform 으로 Cloud SQL for PostgreSQL 16 인스턴스 + Secret Manager 시크릿 3종을 프로비저닝한다.

## 사전 준비 (1회)

1. **GCP 프로젝트 생성** — staging 과 prod 용 별도 프로젝트 권장.
   ```bash
   gcloud projects create auto-workflow-staging-xxx --name="Auto Workflow Staging"
   gcloud config set project auto-workflow-staging-xxx
   ```
2. **결제 계정 연결** — Cloud SQL 은 free tier 아님. 콘솔 또는:
   ```bash
   gcloud billing projects link auto-workflow-staging-xxx --billing-account=YOUR-BILLING-ID
   ```
3. **도구 설치**
   - `terraform` >= 1.6
   - `gcloud` CLI — `gcloud auth application-default login` 완료
4. **본인 공인 IP 확인** (dev 접속용): https://whatismyipaddress.com/

## 인스턴스 생성

```bash
cd Database/deploy/terraform

# 1. tfvars 작성 (gitignore 됨)
cp environments/staging.tfvars.example environments/staging.tfvars
# environments/staging.tfvars 편집:
#   - project_id 를 실제 GCP 프로젝트 ID 로
#   - authorized_networks 에 본인 IP /32 추가

terraform init
terraform plan  -var-file=environments/staging.tfvars
terraform apply -var-file=environments/staging.tfvars
```

최초 apply 는 5~8 분 (Cloud SQL 인스턴스 기동 + API enablement).

## 시크릿 실값 주입

Terraform 은 credential 마스터 키와 JWT 시크릿을 **placeholder 로** 생성한다. 실제 값을 넣어야 한다.

```bash
# Fernet 마스터 키 (ADR-004)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" | \
  gcloud secrets versions add credential-master-key-staging --data-file=-

# JWT 서명 키 (ADR-015)
openssl rand -base64 48 | \
  gcloud secrets versions add jwt-secret-staging --data-file=-
```

**주의**: 이 두 시크릿을 이후 재발급하면 기존 저장 자격증명이 전부 복호화 불가, 기존 JWT 전부 무효. prod 에서는 rotate 시 의도적 downtime 계획 필요.

## 애플리케이션 접속 설정

### 경로 A — Cloud SQL Auth Proxy (권장)

```bash
# cloud-sql-proxy 다운로드 (한 번만)
# https://cloud.google.com/sql/docs/postgres/sql-proxy#install

INSTANCE_CONN=$(cd Database/deploy/terraform && terraform output -raw instance_connection_name)
cloud-sql-proxy --port=5433 "$INSTANCE_CONN" &
```

이후 `DATABASE_URL="postgresql+asyncpg://<user>:<pw>@localhost:5433/auto_workflow"`.

패스워드 가져오기:
```bash
gcloud secrets versions access latest --secret=db-password-staging
```

### 경로 B — 직접 Public IP 접속 (개발만)

`environments/staging.tfvars` 의 `authorized_networks` 에 본인 IP 를 추가해야 연결 가능.

```bash
IP=$(cd Database/deploy/terraform && terraform output -raw instance_public_ip)
PW=$(gcloud secrets versions access latest --secret=db-password-staging)
export DATABASE_URL="postgresql+asyncpg://auto_workflow:${PW}@${IP}:5432/auto_workflow"
export DATABASE_URL_SYNC="postgresql://auto_workflow:${PW}@${IP}:5432/auto_workflow"
```

## 스키마 + 마이그레이션 적용

기존 `migrate.py` 재사용. Cloud SQL 인스턴스에 대해 동일하게 동작.

```bash
DATABASE_URL_SYNC="postgresql://auto_workflow:<pw>@<host>:5432/auto_workflow" \
  python Database/scripts/migrate.py
```

`schemas/001_core.sql` 이 `CREATE EXTENSION IF NOT EXISTS vector` 를 실행 — Cloud SQL Postgres 16 은 pgvector 내장 지원이라 별도 활성화 불필요.

## API_Server / Execution_Engine 연결

`DATABASE_URL` 한 줄만 위 Cloud SQL DSN 으로 바꾸면 나머지 코드 변경 없음 (이미 env-based).

Cloud Run 배포 시 (후속 ADR) 는 `--set-secrets=DATABASE_URL=...,CREDENTIAL_MASTER_KEY=credential-master-key-staging:latest,JWT_SECRET=jwt-secret-staging:latest` 로 주입.

## 비용 관리

- **시연 끝난 뒤 staging 내리기**:
  ```bash
  # staging.tfvars 에 deletion_protection = false 설정 후:
  terraform destroy -var-file=environments/staging.tfvars
  ```
- **prod 는 deletion_protection = true 기본** — `terraform destroy` 가 거부됨. 의도적으로 내릴 때만 변수 flip.
- 사용 안 할 때 인스턴스 stop 가능:
  ```bash
  gcloud sql instances patch auto-workflow-staging --activation-policy=NEVER
  ```

## 트러블슈팅

- **`terraform apply` 첫 실행에 API 미활성 에러**: `terraform apply` 한 번 더. API 활성화는 비동기라 첫 plan 에서 race 가능.
- **`ERROR: permission denied for schema public`**: `auto_workflow` 유저가 DB 생성 시점에 아직 없음. Terraform 이 순서 (instance → db → user) 를 보장하지만 migrate.py 를 너무 일찍 돌리면 발생. `google_sql_user.app` 가 Ready 된 뒤 실행.
- **pgvector 미발견**: `CREATE EXTENSION vector` 가 superuser 권한 필요 — `cloudsqlsuperuser` 는 이를 가지지만 `auto_workflow` 는 권한 없음. `migrate.py` 를 `postgres` 유저 DSN 으로 한 번 돌려 extension 을 먼저 설치.

## 관련 문서

- `docs/context/decisions.md` ADR-018 — 본 구성의 설계 근거
- `Database/scripts/migrate.py` — 마이그레이션 러너
- `Database/schemas/` — 스키마 SQL 원본
- `Database/migrations/` — 증분 변경 이력
