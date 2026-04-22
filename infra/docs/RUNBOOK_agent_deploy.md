# RUNBOOK — AI_Agent Cloud Run GPU 배포

> PLAN_11 PR 5 (`infra/plans/PLAN_11_AI_AGENT_DEPLOY.md`) 의 실제 적용 절차.
> 모든 명령은 `infra/terraform/` 에서 실행.

## 사전 체크

- [ ] GCP `autoworkflowdemo` 프로젝트 인증 (`gcloud auth application-default login`)
- [ ] us-central1 L4 GPU 쿼터 >= 1 (Cloud Run Admin GPU 쿼터 페이지)
- [ ] HuggingFace 계정 + Gemma 4 라이선스 수락 완료 (`HF_TOKEN` 발급)
- [ ] `infra/terraform/environments/staging.tfvars` 에 `agent_*` 값 작성
      (staging.tfvars.example 복사 + REPLACE 치환)

## 1. 부트스트랩 — 리소스 스켈레톤만 먼저 생성

Cloud Run 서비스는 `agent_image_uri` 유효성 검증이 걸려 있어 빈 AR 상태에서
full apply 가 실패한다. 타겟팅으로 저장소·버킷·SA 만 먼저 만든다.

```bash
cd infra/terraform
terraform init

terraform plan \
  -target=google_project_service.agent_apis \
  -target=google_artifact_registry_repository.agent_images \
  -target=google_storage_bucket.agent_models \
  -target=google_service_account.agent \
  -var-file=environments/staging.tfvars

terraform apply \
  -target=google_project_service.agent_apis \
  -target=google_artifact_registry_repository.agent_images \
  -target=google_storage_bucket.agent_models \
  -target=google_service_account.agent \
  -var-file=environments/staging.tfvars
```

출력에서 `agent_artifact_registry_repo`, `agent_models_bucket` 을 메모.

## 2. 모델 가중치 업로드 (GCS)

GGUF 약 13GB. 업로드에 30-60분 소요. 백그라운드 실행 권장.

```bash
# 로컬 임시 디렉토리에 다운로드
mkdir -p ~/.cache/auto_workflow_demo/models
export HF_TOKEN=hf_...   # 노출 금지, 쉘 변수로만

huggingface-cli download unsloth/gemma-4-26B-A4B-it-GGUF \
  gemma-4-26B-A4B-it-Q4_K_M.gguf \
  --local-dir ~/.cache/auto_workflow_demo/models

# GCS 업로드 (parallel composite upload 로 가속)
gsutil -o "GSUtil:parallel_composite_upload_threshold=150M" \
  cp ~/.cache/auto_workflow_demo/models/gemma-4-26B-A4B-it-Q4_K_M.gguf \
     gs://<agent_models_bucket>/gemma-4-26B-A4B-it-Q4_K_M.gguf

# 확인 — 크기가 ~13GB 인지 (Q4_K_M 기준)
gsutil du -h gs://<agent_models_bucket>/gemma-4-26B-A4B-it-Q4_K_M.gguf
```

## 3. 컨테이너 이미지 빌드 + 푸시

```bash
# repo root 에서
PROJECT_ID=autoworkflowdemo
REGION=us-central1
TAG="$(git rev-parse --short HEAD)"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/auto-workflow-agent/agent:${TAG}"

# AR 인증 (최초 1회)
gcloud auth configure-docker "${REGION}-docker.pkg.dev"

# CUDA 빌드 스테이지 때문에 로컬 NVIDIA 드라이버 불필요 — cross-build 가능.
# 단, buildx 는 사용하지 않는다 (CUDA 베이스 멀티아키 미지원).
docker build -f AI_Agent/Dockerfile -t "${IMAGE}" .
docker push "${IMAGE}"
```

## 4. 전체 Terraform apply

`staging.tfvars` 의 `agent_image_uri` 를 방금 푸시한 태그로 업데이트 후:

```bash
terraform plan  -var-file=environments/staging.tfvars
terraform apply -var-file=environments/staging.tfvars
```

플랜에 `google_cloud_run_v2_service.agent` + 2 개 IAM binding 만 `+` 로
보여야 한다. 기존 api/worker 리소스는 변경 0.

## 5. Smoke test

Agent URL 확인:

```bash
AGENT_URL=$(terraform output -raw agent_service_url)
echo "$AGENT_URL"
```

ingress=INTERNAL_ONLY + SA-only invoker 이므로 로컬 laptop 에서 직접 호출 불가.
staging 에서 health 확인하려면:

### A. API_Server SA impersonation (권장)

```bash
API_SA=$(terraform output -raw api_service_account_email)
TOKEN=$(gcloud auth print-identity-token \
  --impersonate-service-account="${API_SA}" \
  --audiences="${AGENT_URL}")

# 첫 호출은 cold start (30s-5min). timeout 여유 확보.
curl -sS --max-time 600 \
  -H "Authorization: Bearer ${TOKEN}" \
  "${AGENT_URL}/v1/health"
# 기대: {"status":"ok","backend":"llamacpp"}
```

### B. API_Server 쪽 배선 완료 후 E2E

본 PR 머지 후 후속 API_Server PR 에서 `AI_AGENT_URL` 환경변수 + OIDC 토큰
exchange 가 구현된다. 그 시점엔 `/api/v1/ai/compose` 요청이 곧 E2E smoke 가
된다.

## 6. 롤백 / 티어다운

staging 전용 (`deletion_protection = false`):

```bash
# 서비스만 내리기
terraform destroy \
  -target=google_cloud_run_v2_service.agent \
  -var-file=environments/staging.tfvars

# 전부 정리 — 주의: 모델 파일이 13GB 다. 재업로드 비용 발생.
terraform destroy -var-file=environments/staging.tfvars
```

prod 는 `deletion_protection = true` 유지.

## 7. 흔한 실패 패턴

| 증상 | 원인 | 조치 |
|---|---|---|
| `terraform apply` 에서 `nvidia.com/gpu` resource 거부 | provider 버전 낮음 | `google-beta >= 5.x` 확인 |
| Cloud Run revision 가 `EXTERNAL: The user-provided container failed to start` | llama-server CUDA symbol 불일치 | Dockerfile 의 `LLAMA_CPP_REF` / CUDA 이미지 태그 정합성 확인 |
| Startup probe 5분 이상 대기 후 실패 | gcsfuse mmap + 전체 layer GPU 업로드 대기 | 로그에서 `llama_model_load_internal: offloaded` 라인 확인. L4 VRAM 부족이면 `agent_n_gpu_layers` 를 < 999 로 내려 CPU fallback 혼합 |
| `PermissionDenied` on AR pull | agent SA 에 `roles/artifactregistry.reader` 미부여 | `terraform state show google_artifact_registry_repository_iam_member.agent_ar_reader` |
| GCS mount stuck on empty `/models` | 모델 업로드가 다른 bucket 에 들어감 | `gsutil ls gs://<bucket>/` 로 객체 이름 확인 — `agent_model_object_name` 와 정확히 일치해야 함 |

## 8. 다음 단계 (본 PR 범위 외)

PR 5 머지 직후 진행할 후속 PR 리스트:

- **API_Server**: `AI_AGENT_URL` env + `AIAgentHTTPBackend` 에 OIDC 토큰
  exchange (google-auth `id_token_credentials`) 추가. Cloud Run 환경에서
  metadata 서버로 부터 ID 토큰 획득.
- **infra**: `api` Cloud Run 서비스에 `AI_AGENT_URL` 환경변수 주입
  (`cloud_run.tf` 의 api container env 블록에 `google_cloud_run_v2_service.agent.uri`
  reference 추가).
- **AI_Agent**: 로컬 모드 (stub/anthropic) 와 동일한 테스트 커버리지를
  유지하도록 integration 테스트 스키마 재확인.
