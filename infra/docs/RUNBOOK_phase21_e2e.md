# Phase 6 Live E2E Runbook — ADR-021 Worker Pools

> PLAN_21 §6.1 전 구간을 단일 러닝 기준으로 묶은 실행 가이드. 첫 live
> E2E 에서만 필요한 순서 — 이후는 `run_e2e_phase21.sh` 만 재실행하면 된다.

## 전제

- Phase 3/4/5/5-b 전부 `main` 에 머지됨 (#85, #94, #95, 현재 PR)
- `gcloud` auth + ADC 완료 (`gcloud auth application-default login`)
- Docker 데몬 기동 + Artifact Registry 에 push 권한
- staging 의 `environments/staging.tfvars` 작성 완료
- staging Cloud SQL 인스턴스 이미 존재 (ADR-018 apply 완료)

## Step 1 — Execution_Engine 이미지 빌드 + push

```bash
cd "$(git rev-parse --show-toplevel)"
REGION=asia-northeast3
PROJECT="$(gcloud config get-value project)"
TAG="phase21-$(git rev-parse --short HEAD)"
IMG="${REGION}-docker.pkg.dev/${PROJECT}/auto-workflow/worker:${TAG}"

docker build -t "$IMG" -f Execution_Engine/Dockerfile Execution_Engine/
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet
docker push "$IMG"
echo "$IMG"  # ← 다음 step 의 ee_image_uri 로 사용
```

## Step 2 — Terraform apply (Memorystore + Worker Pool 프로비저닝)

```bash
cd infra/terraform
# staging.tfvars 에 ee_image_uri = "<위 IMG>" 추가
terraform plan  -var-file=environments/staging.tfvars
terraform apply -var-file=environments/staging.tfvars
```

최초 apply 는 Memorystore 프로비저닝 때문에 **약 5~8 분** 소요.
`google_compute_global_address.private_services_range` / peering 은 이미
ADR-018 apply 때 생성됐으므로 delta 는 Memorystore + Worker Pool + IAM.

`terraform output` 확인 항목:
- `ee_worker_pool_name` — API env `WORKER_POOL_NAME` 에 주입
- `broker_host` — API env `CELERY_BROKER_URL` 조립에 사용

## Step 3 — API_Server 재배포 (새 env 반영)

API Cloud Run 서비스에 `SERVERLESS_EXECUTION_MODE=celery`, `WORKER_POOL_NAME`,
`GCP_PROJECT_ID`, `GCP_REGION`, `CELERY_BROKER_URL` 을 주입한다. CI 배포
가 기본이지만, 수동 재배포는 다음과 같이 한다:

```bash
gcloud run services update "auto-workflow-api-${ENV}" \
  --region="$REGION" \
  --update-env-vars="SERVERLESS_EXECUTION_MODE=celery,\
WORKER_POOL_NAME=auto-workflow-ee-${ENV},\
GCP_PROJECT_ID=${PROJECT},\
GCP_REGION=${REGION},\
CELERY_BROKER_URL=redis://<broker_host>:6379/0"
```

> **주의**: broker_host 는 `terraform output -raw broker_host` 로만 획득.
> stdout 로 echo 해도 되는 값이긴 하지만 (Memorystore 는 RFC1918),
> 습관적으로 캡처 후 사용.

## Step 4-7 — 관찰 (자동)

```bash
# 워크플로우 1개 사전 생성 (condition→merge 2-노드 그래프 권장 — PR #95 의
# tests/test_execute_inline.py TWO_NODE_GRAPH 참조). wf_id 확보.
WF_ID=<생성된 workflow id>
TOKEN=<로그인해서 얻은 access_token>
API_BASE="$(gcloud run services describe auto-workflow-api-${ENV} \
             --region=$REGION --format='value(status.url)')"

bash infra/scripts/run_e2e_phase21.sh "$ENV" "$API_BASE" "$TOKEN" "$WF_ID"
```

기대 출력:
```
[1/3] First /execute — expect wake-up log line and terminal status=success
  execution_id=...
  status=success
[2/3] Back-to-back executes within throttle window (30s default)
  execution_ids=... ...
  statuses=success success
[3/3] Verify throttle — expect exactly 1 'woken' log across all 3 executes
  woken_log_count=1 (within last 10m)

Phase 6.1 live observation passed: 3 executions, 1 wake(s), all success.
```

## Step 8 — Scale-down 검증

현재 구현은 MANUAL scaling. AUTOMATIC 의 idle timeout 이 동작하지 않으므로
scale-down 은 다음 중 하나로 검증한다:

- **간단**: `terraform destroy -target=google_cloud_run_v2_worker_pool.ee` —
  pool 자체 삭제. 비용 0 확인 가능.
- **배포본 유지**: gcloud run worker-pools update 로 `--manual-instance-count=0`
  (Admin API patch) 호출. 이후 Cloud Console 에서 instance_count → 0 확인.

Post-Phase-6 후속 작업으로 Cloud Scheduler + Cloud Functions watchdog 을
추가하면 idle 판단 후 자동 0 전환 가능 (PLAN_21 §6.3 리스크 표).

## 롤백 / 장애 대응

- `woken` 로그가 없음 → WakeWorker IAM 부족 의심. `worker.tf` 의
  `api_wake_permission` IAM 바인딩 apply 여부 확인.
- `status=failed` → `gcloud logging read 'resource.type=cloud_run_revision
  AND resource.labels.service_name=auto-workflow-ee-${ENV}' --freshness=15m`
  로 worker 측 스택 확인. Celery 브로커 연결 실패가 가장 흔함 → Memorystore
  IP 도달성 (VPC peering) 재확인.
- API_Server `DATABASE_URL` 미주입으로 lifespan 실패 → Cloud Run revision
  의 env 확인. Step 3 의 `--update-env-vars` 가 반영됐는지.

## 수용 기준 (PLAN_21 §6.4)

- [ ] 스크립트 3 단계 모두 통과
- [ ] Cloud Logging 에서 worker instance 기동 로그 + task pickup 로그 존재
- [ ] `executions` 테이블의 해당 행 `status='success'`, `node_results` 채워짐
- [ ] 3회 실행 중 `woken` 로그 = 1 (throttle 동작)
- [ ] destroy 후 idle 비용 = Cloud SQL + API 만, Memorystore/Worker 0
- [ ] `infra/reports/REPORT_21_worker_pools.md` 작성 완료 (실측 비용/지연 기입)
