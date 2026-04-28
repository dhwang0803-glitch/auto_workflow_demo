# PLAN_11 · PR 5 — AI_Agent Cloud Run GPU 배포 (infra)

> **브랜치**: `feature/infra-ai-agent-cloudrun-gpu` → `main`
> **작성일**: 2026-04-22
> **상류 PLAN**: `AI_Agent/plans/PLAN_11_HACKATHON_SUBMISSION.md` (해커톤 제출물)
> **의존**: AI_Agent PR 2 (Dockerfile + entrypoint, 머지 완료 #114)

## 1. 목표

`AI_Agent` 컨테이너를 Cloud Run GPU (NVIDIA L4) 에서 구동할 수 있도록
Terraform 리소스 + runbook 을 추가한다. 해커톤 Live demo URL 의 런타임 기반이다.

Exit 조건:
- `terraform plan -var-file=environments/staging.tfvars` 가 신규 리소스만
  `+` 표시로 출력 (기존 리소스 변경 0).
- Runbook 순서대로 적용 시 `curl https://<agent-url>/v1/health` 가 서비스 계정
  ID 토큰으로 200 응답 (실적용은 별도 세션).

## 2. 확정 결정

| 항목 | 결정 | 근거 |
|---|---|---|
| 리전 | `agent_region = us-central1` 별도 변수 | L4 쿼터 us-central1 에만 확보 (memory `reference_cloudrun_gpu_region`). 기존 스택은 `asia-northeast3` 유지 |
| Artifact Registry | `agent_images` 신규 repo (us-central1) | 기존 `auto-workflow` 는 asia-northeast3. cross-region pull cold start 지연 회피 |
| 모델 가중치 | GCS bucket (us-central1) + Cloud Run v2 GCS volume mount | 이미지 beaking 시 12GB+ 이미지 → 레지스트리 비용/업로드 지연. mount 는 gcsfuse 내장 |
| Service Account | `auto-workflow-agent-${env}` 전용 | API/EE SA 와 분리. GCS read + AR read + logging/monitor 만 |
| 공개 invoker | **X** | internal svc-to-svc. `google_service_account.api` → `roles/run.invoker` 만 |
| GPU | `nvidia-l4 × 1`, `gpu_zonal_redundancy_disabled = true` | 해커톤 단일 인스턴스. zonal redundancy off = 비용 절감 |
| Startup probe | `/v1/health` port 8100, `initial_delay=60s`, `failure_threshold=60`, `period=5s` → 최대 ≈ 5분 | 26B-A4B Q4 mmap + KV cache 초기화 30-60s 수용 + 여유 |
| min / max instances | 0 / 1 | L4 쿼터=1 (memory). scale-to-zero 필수 (예산) |
| image drift | `lifecycle.ignore_changes = [template[0].containers[0].image]` | 기존 api/worker 패턴 일치 |

## 3. 리소스 목록

### 신규 파일
- `infra/terraform/ai_agent.tf`
- `infra/docs/RUNBOOK_agent_deploy.md`
- `infra/plans/PLAN_11_AI_AGENT_DEPLOY.md` (본 문서)

### 수정 파일
- `infra/terraform/variables.tf` — `agent_*` 변수 추가
- `infra/terraform/outputs.tf` — `agent_*` output 추가
- `infra/terraform/environments/staging.tfvars.example` — agent 변수 예시
- `infra/terraform/environments/prod.tfvars.example` — agent 변수 예시

### Terraform 리소스 (ai_agent.tf)
1. `google_project_service.agent_apis` — storage API enable (run/AR/iam 는 기존 `runtime_apis` 에서 enable)
2. `google_artifact_registry_repository.agent_images` — us-central1 docker repo
3. `google_storage_bucket.agent_models` — us-central1, uniform access, versioning off
4. `google_service_account.agent` — agent 런타임 SA
5. IAM bindings (agent SA):
   - `roles/logging.logWriter` (project)
   - `roles/monitoring.metricWriter` (project)
   - `roles/artifactregistry.reader` (agent_images repo)
   - `roles/storage.objectViewer` (agent_models bucket)
6. `google_cloud_run_v2_service.agent` — GPU 서비스
7. `google_cloud_run_v2_service_iam_member.api_invokes_agent` — api SA → agent service invoker

## 4. 변수 계약

```hcl
variable "agent_region"              # string, default "us-central1"
variable "agent_image_uri"           # string, REQUIRED (no default)
variable "agent_cpu"                 # string, default "8"
variable "agent_memory"              # string, default "32Gi"
variable "agent_gpu_type"            # string, default "nvidia-l4"
variable "agent_gpu_count"           # number, default 1
variable "agent_min_instances"       # number, default 0
variable "agent_max_instances"       # number, default 1
variable "agent_model_bucket_name"   # string, REQUIRED (global-unique)
variable "agent_model_object_name"   # string, default "gemma-4-26B-A4B-it-UD-Q4_K_M.gguf"
variable "agent_ctx_size"            # number, default 8192
variable "agent_n_gpu_layers"        # number, default 999
```

## 5. 부트스트랩 순서 (runbook 요약)

1. GCS 버킷 + AR repo + SA 먼저 apply (`-target=`):
   ```
   terraform apply -target=google_storage_bucket.agent_models \
                   -target=google_artifact_registry_repository.agent_images \
                   -target=google_service_account.agent \
                   -var-file=environments/staging.tfvars
   ```
2. `huggingface-cli download unsloth/gemma-4-26B-A4B-it-GGUF gemma-4-26B-A4B-it-UD-Q4_K_M.gguf ...`
3. `gsutil cp <local.gguf> gs://<agent_model_bucket_name>/gemma-4-26B-A4B-it-UD-Q4_K_M.gguf`
4. `docker build -f AI_Agent/Dockerfile -t <agent-image-uri> .`
5. `docker push <agent-image-uri>` (us-central1 AR)
6. `agent_image_uri` 을 staging.tfvars 에 세팅 후 전체 apply.
7. `curl -H "Authorization: Bearer $(gcloud auth print-identity-token)" <agent-url>/v1/health`

## 6. 범위 외 (다음 PR)

- **API_Server 쪽 배선** (`AI_AGENT_URL`, ID 토큰 exchange, `AIAgentHTTPBackend` 의
  base_url 런타임 주입) — 본 PR 머지 후 브랜치별 분리된 후속 PR 로 진행.
- **도메인/커스텀 URL** — 해커 톤 제출물의 live demo URL 은 API_Server 의 run.app
  URL 직공개. AI_Agent 는 내부 전용이라 별도 도메인 불필요.
- **Scale-down watchdog** — min=0 + scale-to-zero 가 기본이라 worker pool 처럼
  manual scale-down 필요 없음 (GPU 자체는 request-driven scale-to-zero 지원).

## 7. 리스크 + 완화

| 리스크 | 영향 | 완화 |
|---|---|---|
| L4 쿼터 1 로 bounded → max=1 | 동시성 저하 | 단독 해커 데모 용도. 쿼터 증액은 제출 후 고려 |
| GCS mount 의존 gcsfuse 레이턴시 | cold start 지연 연장 | mmap 후 warm 유지. `failure_threshold=60` (5분) 로 수용 |
| cross-region (agent us-central1 ↔ api asia-northeast3) | latency ~150ms | compose 는 LLM 추론 지배 (1-2s). 상대적 증분 무시 가능 |
| Cloud Run GPU beta 필드 drift | terraform plan noise | `ignore_changes` 에 gpu 관련 필드 포함 X (제공업체 안정화 시 재검토) |
| 모델 upload 지연 (~13GB @ 30-60분) | 부트스트랩 시간 | runbook 에 명시 + 백그라운드 실행 가이드 |

## 8. 관련

- `docs/context/decisions.md` — ADR-020 Cloud Run v2 + Artifact Registry 패턴 (원형)
- `docs/context/decisions.md` — ADR-021 Worker Pools 패턴 (SA 분리 원칙)
- auto-memory `project_gemma4_model_decisions.md` — 모델/서빙 선정 근거
- auto-memory `reference_cloudrun_gpu_region.md` — 리전/쿼터 근거
