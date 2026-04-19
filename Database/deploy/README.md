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

## 시크릿 R/W 패턴

**규칙 한 줄**: 시크릿 값을 **사람 눈에 보이는 stdout 에 절대 내보내지 않는다**. 쓸 때는 파이프, 읽을 때는 쉘 변수 캡처.

### 쓰기 — 시크릿 실값 주입

Terraform 은 credential 마스터 키와 JWT 시크릿을 **valid-but-placeholder 로** 생성한다 (이전에는 `REPLACE_ME_*` 평문이었으나 컨테이너 Fernet 초기화가 실패해서 base64-valid 더미로 전환). 실제 값을 넣어야 한다.

```bash
# Fernet 마스터 키 (ADR-004) — 생성 결과를 절대 print/echo 하지 말고 바로 파이프
python -c "from cryptography.fernet import Fernet; import sys; sys.stdout.write(Fernet.generate_key().decode())" | \
  gcloud secrets versions add credential-master-key-staging --data-file=-

# JWT 서명 키 (ADR-015)
python -c "import secrets, sys; sys.stdout.write(secrets.token_urlsafe(48))" | \
  gcloud secrets versions add jwt-secret-staging --data-file=-
```

**주의**: 이 두 시크릿을 이후 재발급하면 기존 저장 자격증명이 전부 복호화 불가, 기존 JWT 전부 무효. prod 에서는 rotate 시 의도적 downtime 계획 필요.

### 읽기 — 시크릿 재사용 시

`gcloud secrets versions access latest --secret=<id>` 의 **stdout 을 터미널에 그대로 뿌리면** 평문이 스크롤백, 셸 히스토리, 에이전트 대화 로그(JSONL) 까지 모두 잔존한다. 무조건 `$(...)` 로 쉘 변수에 캡처해서 바로 다음 명령의 env 로 넘긴다.

```bash
# ❌ 나쁜 패턴 — 비밀번호가 스크롤백에 남는다
gcloud secrets versions access latest --secret=db-password-prod
# → gsAW6wOy4dgugAKCrhqunqT27tIMENu8   ← 영구 보존

# ✅ 좋은 패턴 — 변수에만 담고 바로 소비
PW="$(gcloud secrets versions access latest --secret=db-password-prod --project=$P)"
export DATABASE_URL_SYNC="postgresql://auto_workflow:${PW}@127.0.0.1:15432/auto_workflow"
python Database/scripts/migrate.py
unset PW
```

migrate 는 래퍼 스크립트를 쓰면 위 패턴이 이미 물리화돼 있다:
```bash
Database/deploy/scripts/migrate_via_proxy.sh prod --status
Database/deploy/scripts/migrate_via_proxy.sh prod          # apply pending
```
이 래퍼는 proxy 기동 → 시크릿 변수 캡처 → migrate 실행 → proxy cleanup 을 모두 처리하며 DB 비밀번호를 stdout/argv/로그 어디에도 남기지 않는다.

### 개발자 workstation 위생

시크릿 값이 터미널에 한 번이라도 찍혔다면 다음을 고려:
- **PowerShell**: `Clear-History` + `Remove-Item (Get-PSReadlineOption).HistorySavePath`
- **bash/zsh**: `history -c && history -w` + `shred -u ~/.bash_history ~/.zsh_history`
- **터미널 스크롤백**: 터미널 종료/재시작
- **에이전트 로그**: Claude Code / Copilot 등은 JSONL 에 full transcript 보존 — 해당 세션 파일 삭제 필요 (동기화 폴더에 걸려 있으면 그쪽도)
- **노출이 prod 시크릿이었으면**: 즉시 rotate. Cloud Run 은 `value_source.secret_key_ref` 의 `version = "latest"` 를 cold start 에서 픽업하므로 새 버전 추가 후 revision 강제 재배포.

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

### Destroy 소요 시간 예산

`terraform destroy` 는 과금 리소스 (Cloud SQL, Cloud Run, AR, secrets) 는 2~5분이면 끝나지만 **Cloud Run Direct VPC Egress 가 남기는 `serverless-ipv4-*` 주소 예약 GC 때문에 VPC/subnet/service-networking 해제가 10~30분 걸린다** (GCP 내부 reconciler 의존, CLI 강제 해제 경로 없음). 시연 중간에 destroy 를 끼워넣지 말 것 — 라이브 데모 직전 또는 직후 시간 예산 **45분** 잡고 진행.

증상: `subnetwork ... is already being used by .../addresses/serverless-ipv4-*` 또는 `Service Networking Connection: Producer services are still using this connection`.

우회: 폴링 스크립트로 재시도.
```bash
# 주소가 해제될 때까지 재시도 (최대 ~40분)
for i in $(seq 1 40); do
  gcloud compute addresses delete "serverless-ipv4-*" \
    --region="$REGION" --project="$PROJECT" --quiet 2>/dev/null && break
  sleep 60
done
terraform destroy -var-file=environments/prod.tfvars  # 남은 VPC/peering 마감
```

## 트러블슈팅

- **`terraform apply` 첫 실행에 API 미활성 에러**: `terraform apply` 한 번 더. API 활성화는 비동기라 첫 plan 에서 race 가능.
- **`ERROR: permission denied for schema public`**: `auto_workflow` 유저가 DB 생성 시점에 아직 없음. Terraform 이 순서 (instance → db → user) 를 보장하지만 migrate.py 를 너무 일찍 돌리면 발생. `google_sql_user.app` 가 Ready 된 뒤 실행.
- **pgvector 미발견**: `CREATE EXTENSION vector` 가 superuser 권한 필요 — `cloudsqlsuperuser` 는 이를 가지지만 `auto_workflow` 는 권한 없음. `migrate.py` 를 `postgres` 유저 DSN 으로 한 번 돌려 extension 을 먼저 설치.

## Cloud Run 배포 (ADR-020)

ADR-020 에서 API_Server 를 Cloud Run 으로 배포한다. Terraform 이 인프라(VPC + Cloud Run 서비스 + AR + SA + IAM + Auth Proxy 사이드카)를 모두 찍고, 이미지 갱신만 CI / 수동 `gcloud` 에서 담당.

### 사전 준비 — Workload Identity Federation (WIF, 1회)

GH Actions → GCP 인증은 **서비스 계정 키 JSON 없이** WIF OIDC 로만. 키 파일 유출·순환 이슈 제거.

```bash
PROJECT_ID=auto-workflow-prod-REPLACE
POOL=github-pool
PROVIDER=github-actions
REPO=dhwang0803-glitch/auto_workflow_demo   # owner/name

# 1. Workload Identity Pool + OIDC provider
gcloud iam workload-identity-pools create "$POOL" \
  --project="$PROJECT_ID" --location=global

gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" \
  --project="$PROJECT_ID" --location=global \
  --workload-identity-pool="$POOL" \
  --display-name="GitHub Actions" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository == '${REPO}'" \
  --issuer-uri="https://token.actions.githubusercontent.com"

# 2. CI 용 SA (Cloud Run admin + AR writer + impersonate Cloud Run runtime SA)
SA_CI=auto-workflow-ci@${PROJECT_ID}.iam.gserviceaccount.com
gcloud iam service-accounts create auto-workflow-ci --project="$PROJECT_ID"

for role in roles/run.admin roles/artifactregistry.writer roles/iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SA_CI}" --role="$role"
done

# 3. release 브랜치에서만 이 SA 를 가장할 수 있게 binding
POOL_ID=$(gcloud iam workload-identity-pools describe "$POOL" \
  --project="$PROJECT_ID" --location=global --format='value(name)')
gcloud iam service-accounts add-iam-policy-binding "$SA_CI" \
  --project="$PROJECT_ID" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/${POOL_ID}/attribute.repository/${REPO}"
```

결과로 얻는 값 2개를 GitHub 저장소에 등록:

- **Settings → Secrets → Actions**:
  - `GCP_WIF_PROVIDER` = `${POOL_ID}/providers/${PROVIDER}` (전체 resource 경로)
  - `GCP_WIF_SERVICE_ACCOUNT` = `auto-workflow-ci@<project>.iam.gserviceaccount.com`
- **Settings → Variables → Actions**:
  - `GCP_PROJECT_ID_PROD` = `auto-workflow-prod-…`
  - `GCP_REGION` = `asia-northeast3`

### 배포 브랜치 + 보호 규칙 (1회)

```bash
# main 기준으로 두 브랜치 생성
git checkout main && git pull
git push origin main:development
git push origin main:release
```

GitHub → Settings → Branches → Add rule:

| 브랜치 | 규칙 |
|---|---|
| `release` | Require a pull request before merging · **Require linear history** · Require status checks to pass · Do not allow force pushes · Do not allow deletions |
| `development` | Require a pull request before merging · Do not allow force pushes |

`release` 의 PR merge 옵션은 **Rebase and merge** 또는 **Squash and merge** 만 허용(`Allow merge commits` OFF) 하면 linear history 가 강제됨.

### 부트스트랩 — 첫 `terraform apply` (Phase 4 기준)

`api_image_uri` 는 필수 변수 (ADR-020 §6-a). AR 이 먼저 있어야 이미지 푸시가 되고, 이미지가 있어야 `/health` probe 가 통과하므로 2-단계로 진행한다.

```bash
cd Database/deploy/terraform

# 1) API enablement + Artifact Registry 만 먼저 apply
terraform apply -var-file=environments/staging.tfvars \
  -target=google_project_service.runtime_apis \
  -target=google_artifact_registry_repository.images

# 2) 이미지 빌드 + 푸시 (로컬)
AR="asia-northeast3-docker.pkg.dev/${PROJECT_ID}/auto-workflow/api"
TAG=bootstrap-$(date +%Y%m%d)
gcloud auth configure-docker asia-northeast3-docker.pkg.dev --quiet
docker build -f API_Server/Dockerfile -t "${AR}:${TAG}" .
docker push "${AR}:${TAG}"

# 3) staging.tfvars 의 api_image_uri 를 "${AR}:${TAG}" 로 바꾸고 전체 apply
terraform apply -var-file=environments/staging.tfvars
```

이후 apply 는 단일 단계로 끝남. `lifecycle.ignore_changes` 덕분에 CI 나 수동 `gcloud run deploy` 로 바뀐 이미지는 다음 apply 에서 revert 되지 않는다.

### 개발 서버 수동 배포 (`development` 브랜치)

staging GCP 프로젝트의 Cloud Run 서비스 (`auto-workflow-api-staging`) 로 사람이 직접 배포.

```bash
git checkout development && git pull
git merge --ff-only main        # main 에서 올라온 변경만 먼저 받기

SHA=$(git rev-parse HEAD)
PROJECT=auto-workflow-staging-REPLACE
REGION=asia-northeast3
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/auto-workflow/api:${SHA}"

gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet
docker build -f API_Server/Dockerfile -t "$IMAGE" .
docker push "$IMAGE"

gcloud run deploy auto-workflow-api-staging \
  --image="$IMAGE" \
  --region="$REGION" \
  --project="$PROJECT" \
  --quiet

git push origin development      # 브랜치 포인터 업데이트
```

이 단계에서 로그·에러·응답을 확인. 통과하면 `release` 로 승격.

### 운영 서버 자동 배포 (`release` 브랜치 + GH Actions)

`development` 에서 검증된 커밋을 `release` 로 ff-only 승격.

```bash
git checkout release && git pull
git merge --ff-only development
git push origin release          # ff-only 가 아니면 protection 이 거부함
```

push 가 성공하면 `.github/workflows/deploy-prod.yml` 이 자동 실행:
1. linearity guard (merge commit 이면 실패)
2. WIF 로 GCP 인증
3. AR 로그인 → `docker build/push` (태그 = `${{ github.sha }}`)
4. `gcloud run deploy auto-workflow-api-prod --image=<tag>`
5. Cloud Run 이 새 revision 을 `/health` probe 까지 확인하고 트래픽 스위치. probe 실패 시 gcloud 가 non-zero 로 종료 → workflow 실패 → 이전 revision 유지.

### 롤백

prod 에서 회귀 발견 시:

```bash
git checkout release
git revert <bad-sha>             # revert 커밋 생성
git push origin release          # 같은 workflow 가 이전 상태를 다시 배포
```

또는 Cloud Run 콘솔 · `gcloud run services update-traffic auto-workflow-api-prod --to-revisions=<prev>=100` 로 즉시 트래픽만 이전 revision 으로 돌린 뒤 git revert 를 따로 진행해도 됨.

### 긴급 hotfix

`main` 리뷰/머지 과정을 단축할 필요가 있으면 `hotfix/*` 브랜치에서 `release` 로 ff-only 머지 후 바로 `main` 과 `development` 로 역방향 동기화. 이 경우에도 `release` 는 linear 를 유지한다.

## 관련 문서

- `docs/context/decisions.md` ADR-018 — Cloud SQL + Secret Manager
- `docs/context/decisions.md` ADR-020 — Cloud Run 배포 (§1-10) + §6-a `api_image_uri` 정책 + §7 브랜치 전략
- `Database/scripts/migrate.py` — 마이그레이션 러너
- `Database/schemas/` — 스키마 SQL 원본
- `Database/migrations/` — 증분 변경 이력
- `.github/workflows/deploy-prod.yml` — release 자동 배포 파이프라인
