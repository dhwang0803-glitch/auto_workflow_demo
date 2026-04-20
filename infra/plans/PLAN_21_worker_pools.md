# PLAN_21 — Execution_Engine 배포 (Cloud Run Worker Pools + Memorystore Redis)

> **브랜치**: `infra` (Terraform/IAM) + `Execution_Engine` (worker 보정) + `API_Server` (inline + wake-up) · **작성일**: 2026-04-20 · **상태**: Draft
>
> ADR-021 (docs 브랜치, PR #91 머지) 이 결정한 구조의 실행 분해. Phase 1 (ADR) / Phase 2 (본 PLAN) 는 문서 단계, Phase 3~6 이 실구현. 본 PLAN 은 **Phase 3~6 을 1:1 매핑**하여 파일·리소스·테스트 게이트를 고정한다. ADR 과 차이가 생기면 ADR Update 섹션으로 먼저 수정 후 본 PLAN 재조정.

## 1. 목표

1. Memorystore Redis Basic 1GB 인스턴스 + Cloud Run Worker Pools (min=0 max=5) 를 Terraform 으로 기술
2. Execution_Engine Celery worker 가 Memorystore 를 broker 로 동작 (로컬 Docker Redis 경로는 유지)
3. API_Server 가 execute 트리거 시 Cloud Run Admin API `services.patch` 로 Worker Pools wake-up 수행 + throttle
4. `execution_mode = inline` 임시 스톱갭 완성 후 Phase 6 에서 **전면 제거**
5. staging 에서 full E2E (`/execute` → wake → queue → worker → DB 기록) 성공 후 destroy 싸이클 검증

## 2. 범위

**In**
- `infra/terraform/memorystore.tf` (신규), `worker.tf` (신규), `variables.tf` 확장, `outputs.tf` 확장
- IAM: API_Server SA 에 `run.workerPools.update` 제한 바인딩 (IAM condition 으로 특정 worker pool 리소스만)
- `infra/scripts/deploy_worker_pool.sh` (이미지 빌드 + push + apply wrapper)
- Execution_Engine: `scripts/worker.py` 브로커 URL 환경변수화 + soft timeout + SETNX idempotency
- API_Server: `settings.execution_mode` 스위치, `services/workflow_service.py::execute_workflow` inline 분기, `services/wake_worker.py` (신규) Cloud Run patch wrapper + throttle
- bats 단위 테스트 (Phase 3), pytest (Phase 4/5), live E2E bash (Phase 6)
- `infra/docs/README.md` 에 "Worker Pools 배포 runbook" 섹션 추가

**Out**
- GPU / LLM inference 경로 (ADR-008) — 별도 ADR
- Agent 모드 배포 — 별도 ADR (ADR-023 예정)
- Cloud Monitoring 커스텀 메트릭 기반 queue-depth autoscaling — ADR-021 §4 에서 기각, 본 PLAN 에서도 다루지 않음
- Celery Beat (APScheduler 대체) 전환 — ADR-021 §10 에서 deferred
- Frontend Phase C 자체 — 본 PLAN 은 inline 모드를 제공할 뿐, Phase C E2E 구현은 별도 Frontend PLAN
- Memorystore Standard 티어 승격 — prod 진입 시 별도 ADR Update

## 3. Phase 3 — Terraform (infra 브랜치)

### 3.1 신규 파일

**`infra/terraform/memorystore.tf`**

```hcl
resource "google_redis_instance" "broker" {
  name               = "auto-workflow-broker-${var.environment}"
  tier               = "BASIC"
  memory_size_gb     = 1
  region             = var.region
  authorized_network = google_compute_network.vpc.id
  connect_mode       = "PRIVATE_SERVICE_ACCESS"
  redis_version      = "REDIS_7_2"
  reserved_ip_range  = google_compute_global_address.service_networking.name

  lifecycle {
    prevent_destroy = true   # Basic 은 삭제 보호 없어 Terraform 가드로만 방어
  }
}
```

- `connect_mode = "PRIVATE_SERVICE_ACCESS"` — ADR-020 이 이미 만든 `google-managed-services-*` allocated range 재사용 (새 subnet 불필요)
- `redis_version` 은 Memorystore 지원 최신 안정 버전 고정 (staging 과 prod 동일)

**`infra/terraform/worker.tf`**

```hcl
resource "google_cloud_run_v2_worker_pool" "ee" {
  provider = google-beta   # Worker Pools 는 일부 리전에서 beta 필요, GA 확인 후 google 로 전환
  name     = "auto-workflow-ee-${var.environment}"
  location = var.region

  template {
    service_account = google_service_account.ee_runtime.email

    containers {
      image = var.ee_image_uri   # AR 경로, 필수 — ADR-020 의 api_image_uri 와 동일 패턴
      resources {
        limits = { cpu = "0.5", memory = "512Mi" }
      }

      env {
        name  = "CELERY_BROKER_URL"
        value = "redis://${google_redis_instance.broker.host}:${google_redis_instance.broker.port}/0"
      }
      env {
        name  = "DATABASE_URL"
        value_source { secret_key_ref { secret = google_secret_manager_secret.database_url.secret_id, version = "latest" } }
      }
      env {
        name  = "CREDENTIAL_MASTER_KEY"
        value_source { secret_key_ref { secret = google_secret_manager_secret.credential_master_key.secret_id, version = "latest" } }
      }
      env {
        name  = "GOOGLE_OAUTH_CLIENT_ID"
        value_source { secret_key_ref { secret = google_secret_manager_secret.google_oauth_client_id.secret_id, version = "latest" } }
      }
      env {
        name  = "GOOGLE_OAUTH_CLIENT_SECRET"
        value_source { secret_key_ref { secret = google_secret_manager_secret.google_oauth_client_secret.secret_id, version = "latest" } }
      }
    }

    vpc_access {
      network_interfaces {
        network    = google_compute_network.vpc.id
        subnetwork = google_compute_subnetwork.cloudrun_direct.id   # API_Server 와 공유
      }
      egress = "PRIVATE_RANGES_ONLY"
    }

    scaling {
      min_instance_count = 0
      max_instance_count = 5
    }
  }

  lifecycle {
    ignore_changes = [
      # wake-up 으로 인한 instance_count 변화는 Terraform state drift 로 잡지 말 것
      template[0].scaling[0].min_instance_count,
    ]
  }
}

# API_Server SA 에 Worker Pools patch 권한
resource "google_cloud_run_v2_worker_pool_iam_member" "api_wake_permission" {
  provider = google-beta
  project  = var.project_id
  location = google_cloud_run_v2_worker_pool.ee.location
  name     = google_cloud_run_v2_worker_pool.ee.name
  role     = "roles/run.developer"   # workerPools.update 포함 최소 role, 추후 custom role 로 축소 가능
  member   = "serviceAccount:${google_service_account.api_runtime.email}"
}
```

**`infra/terraform/variables.tf`** — 추가 변수 3 개

```hcl
variable "ee_image_uri" {
  description = "Execution_Engine 이미지 경로 (AR full path). 비워두면 apply 전 에러."
  type        = string
}

variable "ee_worker_max_instances" {
  description = "Worker Pools max_instance_count"
  type        = number
  default     = 5
}

variable "ee_worker_resources" {
  description = "Worker 컨테이너 리소스 limits"
  type        = object({ cpu = string, memory = string })
  default     = { cpu = "0.5", memory = "512Mi" }
}
```

**`infra/terraform/outputs.tf`** — 2 개 추가

```hcl
output "ee_worker_pool_name" {
  value       = google_cloud_run_v2_worker_pool.ee.name
  description = "API_Server 환경변수 WORKER_POOL_NAME 으로 주입"
}

output "broker_host" {
  value       = google_redis_instance.broker.host
  description = "Memorystore host (API_Server CELERY_BROKER_URL 조립용)"
  sensitive   = false
}
```

**`infra/terraform/cloud_run.tf` 수정 (API_Server)** — 새 env 추가

```hcl
env {
  name  = "WORKER_POOL_NAME"
  value = google_cloud_run_v2_worker_pool.ee.name
}
env {
  name  = "CELERY_BROKER_URL"
  value = "redis://${google_redis_instance.broker.host}:${google_redis_instance.broker.port}/0"
}
```

### 3.2 tfvars example 갱신

`staging.tfvars.example` / `prod.tfvars.example` 에 `ee_image_uri` 추가. 실값은 gitignore 대상 tfvars 에 주입.

### 3.3 수용 기준

- `terraform validate` + `terraform plan -var-file=staging.tfvars.example` 성공 (dry-run)
- `tflint` + `checkov` infra 규칙 통과 (이미 CI 에 있는 것 재사용)
- `bats` 테스트 신규 2 개 추가: Memorystore authorized_network 검증, Worker Pools scaling block 검증
- `infra/docs/README.md` 에 Worker Pools 배포 runbook 섹션 (≤50 라인) — 이미지 빌드·push·apply·로그 확인 순서

## 4. Phase 4 — Execution_Engine (Execution_Engine 브랜치)

### 4.1 파일 수정

**`Execution_Engine/scripts/worker.py`**
- Celery broker URL 을 `os.environ["CELERY_BROKER_URL"]` 에서 읽기 (현재 하드코딩 경로 제거)
- `CELERYD_TASK_SOFT_TIME_LIMIT = 8` (SIGTERM 10초 grace 안쪽 마진)
- `CELERYD_TASK_TIME_LIMIT = 30` (hard kill 상한)
- SIGTERM 핸들러는 Celery 기본 `warm_shutdown` 이용 — 명시 코드 추가 불필요, 로그 라인만 한 줄 (`logger.info("SIGTERM received, warm shutdown")`) 로 관측성 확보

**`Execution_Engine/src/dispatcher/serverless.py`**
- 태스크 진입점 wrapper 에 **SETNX idempotency** 추가:

```python
async def execute_with_idempotency(execution_id: UUID, ...):
    key = f"execution:{execution_id}"
    if not await redis.set(key, "running", nx=True, ex=86400):
        logger.info("execution %s already running/completed, skipping duplicate", execution_id)
        return
    try:
        return await _do_execute(...)
    finally:
        await redis.set(key, "completed", ex=86400)
```

- Redis 클라이언트는 `redis.asyncio.from_url(os.environ["CELERY_BROKER_URL"])` 모듈 싱글톤 (broker 와 동일 인스턴스 공유)

**`Execution_Engine/tests/test_dispatcher_idempotency.py` (신규)**
- 3 케이스: 첫 호출 실행 / 동시 2 호출 → 1 회만 실행 / 이전 실행 완료 후 같은 id 재호출 → skip
- fakeredis 로 로컬 검증 (`pip install fakeredis`)

### 4.2 수용 기준

- `pytest Execution_Engine/tests/` 전부 green (기존 + 신규 3 케이스)
- `scripts/worker.py` 로컬 `docker-compose up redis worker` 로 실행 → fake task enqueue → pickup 로그 확인 (수동)
- broker URL 미설정 시 명확한 에러 메시지 (`KeyError: CELERY_BROKER_URL`) 로 fail fast

## 5. Phase 5 — API_Server inline (임시) + 5-b wake-up (API_Server 브랜치)

### 5.1 Phase 5 — inline 모드 (임시)

**`API_Server/app/config.py`**
```python
execution_mode: Literal["celery", "inline"] = Field(default="celery", description="임시 — ADR-021 Phase 6 종료 시 제거")
```

**`API_Server/app/services/workflow_service.py::execute_workflow`**
- `settings.execution_mode == "inline"` 분기에서 `runtime.executor.execute_dag(...)` 를 await 로 직접 호출, 실행 결과 동기 반환
- `"celery"` 분기는 기존 Celery `.delay()` 유지 + §5.2 의 wake-up 호출

**테스트** — `tests/test_execute_inline.py` (신규)
- 3 노드 DAG inline 실행 → 결과 동기 반환 확인
- 타임아웃 노드는 inline 에서 `WorkflowTimeoutError` 발생 확인

### 5.2 Phase 5-b — wake-up wiring (celery 모드)

**`API_Server/app/services/wake_worker.py` (신규)**

```python
import time
from google.cloud import run_v2

_last_wake_at: float = 0.0
_WAKE_THROTTLE_S = 30.0   # 30초 내 중복 wake skip

async def wake_worker_pool() -> None:
    global _last_wake_at
    now = time.monotonic()
    if now - _last_wake_at < _WAKE_THROTTLE_S:
        return   # warm 가정, patch 호출 생략
    client = run_v2.WorkerPoolsAsyncClient()
    pool_name = f"projects/{settings.project_id}/locations/{settings.region}/workerPools/{settings.worker_pool_name}"
    await client.update_worker_pool(
        worker_pool=run_v2.WorkerPool(
            name=pool_name,
            template=run_v2.WorkerPoolRevisionTemplate(
                scaling=run_v2.WorkerPoolScaling(min_instance_count=1)
            ),
        ),
        update_mask={"paths": ["template.scaling.min_instance_count"]},
    )
    _last_wake_at = now
    logger.info("worker pool %s woken", settings.worker_pool_name)
```

- Worker Pools 는 task 처리 없으면 idle timeout (기본 15분) 후 자동 0 으로 회귀 → 명시적 sleep 호출 불필요
- 30초 throttle: 연속 요청 때 patch API 폭풍 방지. 30초면 이미 warm 상태 가정이 안전

**`execute_workflow` `celery` 분기**
```python
if settings.execution_mode == "celery":
    await wake_worker_pool()
    execute_task.delay(workflow_id, ...)
```

### 5.3 수용 기준

- 유닛: `tests/test_wake_worker.py` — 첫 호출 patch 1회, 즉시 재호출 skip, 30초 경과 후 재호출 patch 1회 (monotonic mock)
- 계약: patch 실패 (네트워크/권한) 시 **`execute_workflow` 전체 실패** 가 아닌 로그 + fallback (task 는 큐잉 계속, 다음 호출이 wake 재시도). Celery task 자체는 워커 기동 지연으로 수 분 대기해도 순차 pickup.
- 보안: API SA 가 가진 `roles/run.developer` 범위 → 동일 프로젝트의 다른 worker pool 건드리지 않도록 `settings.worker_pool_name` 은 deployer 가 주입한 정확한 이름만 수용

## 6. Phase 6 — Live E2E + inline 제거 + destroy 검증 (infra 브랜치)

### 6.1 Live E2E 순서 (staging)

1. EE 이미지 빌드 + AR push → `ee_image_uri` tfvars 주입
2. `terraform apply -var-file=staging.tfvars` → Memorystore + Worker Pools 프로비저닝 (~5 분)
3. API 재배포 (`WORKER_POOL_NAME`, `CELERY_BROKER_URL` 새 env 반영)
4. `/workflows/{id}/execute` 호출 → API 로그에서 `worker pool ... woken` 확인
5. Cloud Logging 에서 Worker Pools 인스턴스 기동 로그 + Celery task pickup 로그 추적
6. DB `executions` 테이블에서 실행 상태 `succeeded` 확인
7. 재호출 2 회 더 → throttle 30초 내 재호출은 wake 생략, warm pickup 확인
8. 15분 대기 → Worker Pools instance_count → 0 회귀 확인 (Cloud Console)

### 6.2 Inline 모드 제거

- `settings.execution_mode` 필드 삭제 + inline 분기 + `test_execute_inline.py` 파일 삭제
- CI 에 `.github/workflows/inline-guard.yml` 추가:
  ```yaml
  - run: |
      if grep -rn "execution_mode" API_Server/app/ Frontend/src/; then
        echo "inline mode residual detected" && exit 1
      fi
  ```
- `ADR-021` Phase 표의 Phase 5/5-b/6 상태를 `✅` 로 갱신 (docs 브랜치 별도 PR)

### 6.3 Destroy 싸이클 검증

- `terraform destroy -var-file=staging.tfvars -target=google_redis_instance.broker -target=google_cloud_run_v2_worker_pool.ee` 로 부분 destroy (Cloud SQL 은 유지)
- `prevent_destroy = true` 로 가드된 Memorystore 는 destroy 차단 확인 → 해제 후 재destroy
- 총 destroy 시간 기록 (ADR-021 Consequences 에 실측 추가 예정 — Cloud Run Direct VPC Egress 의 `serverless-ipv4-*` GC 지연이 Worker Pools 에도 적용되는지 확인)

### 6.4 수용 기준

- Live E2E step 4~8 전부 성공
- Inline guard CI green
- destroy 후 idle 비용 확인 — Cloud SQL (`db-g1-small`) + Cloud Run API_Server min=1 만 남음, Memorystore 과금 0
- `infra/reports/REPORT_21_worker_pools.md` 작성 (실측 비용, 회귀, 교훈 — REPORTER 에이전트 출력 포맷)

## 7. Phase 간 의존성 & 브랜치 경계

```
Phase 3 (infra)   ┐
                  ├─→ Phase 6 (infra: E2E + destroy)
Phase 4 (EE)      ┤        ↑
                  │        │
Phase 5 (API)     ┘        │
                           │
Phase 5-b (API) ←──────────┘  (worker_pool_name output 필요)
```

- Phase 3 의 `ee_worker_pool_name` output → Phase 5-b 의 `settings.worker_pool_name` 으로 연결 → API 재배포 시 env 주입
- Phase 4 와 Phase 5 는 독립 (서로 다른 브랜치) — 병렬 PR 가능
- Phase 6 는 Phase 3/4/5/5-b 전부 머지 후에만 시작. 순서 어기면 wake-up 이 없는 worker 를 때림 (fail)

## 8. 리스크 & 완화

| 리스크 | 완화 |
|---|---|
| Worker Pools SKU 가 여전히 beta 단계인 리전 / 제약 | `google-beta` provider 명시 + `terraform plan` 단계에서 조기 실패. 이슈 발견 시 ADR-021 Update 섹션에 기록 |
| `run.workerPools.update` IAM 범위가 의도보다 넓음 (`roles/run.developer`) | 실배포 검증 후 custom role `workerPool.updateOnly` 로 축소 — Phase 6 후속 작업 |
| Memorystore `prevent_destroy = true` 로 destroy 막혀서 CI 블록 | `-target` 없이 전체 destroy 시도 실패 → 래퍼 스크립트가 `prevent_destroy` 해제 옵션 제공 (`--force-destroy-broker`) |
| wake-up 실패 시 task 가 무한 대기 | wake_worker 실패 → 로그 + Celery task 는 정상 enqueue. Worker 가 기동될 때 pickup 됨. 실사용에서 "wake 실패 5회 연속" 발생 시 Phase 6 이후 retry 로직 검토 |
| Worker Pools cold start 10~20초 가 시연 체감 저하 | Frontend progress UI 에서 "실행 큐에 배치됨" 문구 표시 (Frontend PLAN 에 계약 명시) |
| inline 모드 코드가 제거되지 못하고 잔존 | CI `inline-guard.yml` + Phase 6 PR 리뷰 체크리스트. docs 브랜치에 ADR-021 상태 업데이트 강제 |
| Redis SETNX idempotency key TTL 24h 가 충돌 유발 (같은 execution_id 24h 내 재실행 시도) | execution_id 는 UUID → 충돌 확률 0. 재시도는 별도 execution row → 새 UUID. OK |

## 9. 작업 순서 (실작업용)

1. [infra] Phase 3 TF 파일 작성 + `terraform validate/plan` + bats 테스트 → PR
2. 머지 후 [EE] Phase 4 broker URL 교체 + SETNX idempotency + pytest → PR (병렬 가능)
3. 머지 후 [API] Phase 5 inline 모드 + 테스트 → PR
4. 머지 후 [API] Phase 5-b wake-up 모듈 + throttle 테스트 → PR
5. 머지 후 [infra] Phase 6 Live E2E → REPORT 작성 → ADR-021 Phase 표 ✅ 갱신 PR (docs)
6. 머지 후 [API] inline 제거 + CI guard → PR

총 6 PR 예상. ADR-021 이 이미 머지된 후 본 PLAN PR 은 7 번째, 합계 8 PR 로 ADR-021 전 과정 종료.

## 10. Related

- ADR: [`ADR-021`](../../docs/context/decisions.md) (docs 브랜치 PR #91)
- 선행 ADR: ADR-003 (Celery + Redis broker), ADR-018 (VPC + Service Networking), ADR-020 (Cloud Run 배포 패턴)
- 관련 PLAN: API_Server `PLAN_03_EXECUTION_TRIGGER.md` (Celery task 엔트리포인트 계약), Database `PLAN_07_DB_RESILIENCE.md` (Worker 가 DB 커넥션 풀 공유 시 타임아웃 전제)
- 후속 ADR 예정: ADR-022 (Frontend 배포 — inline 모드 수명과 맞물림), ADR-023 (Agent 배포 — 본 Worker Pools 경로와 독립)
