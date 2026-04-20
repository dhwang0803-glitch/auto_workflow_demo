# ADR-021 Phase 6 — Worker Pool Wake-up 실전 디버깅 기록

**날짜:** 2026-04-20
**대상 환경:** staging (`autoworkflowdemo` / asia-northeast3)
**목표:** Cloud Run Worker Pools 로 배포된 Execution_Engine 을 API_Server 가 `/execute` 호출 시 깨우는 live 파이프라인 검증 — PLAN_21 §6.1 (3 회 실행 + wake throttle 확인)
**결과:** 3/3 executions `status=success`, `woken_log_count=1` (30초 throttle 검증됨). **3-layer 근본 원인이 순차적으로 드러남.**

---

## 1. 기대했던 흐름 vs 실제 경험

### 기대

```
docker build + push  →  terraform apply  →  API redeploy
                                                   │
                                                   ▼
    bash infra/scripts/run_e2e_phase21.sh staging ... WF_ID
                                                   │
                                                   ▼
    3 executions all success, 1 wake log, done.
```

실행 스크립트는 이전 Phase 6 prep (commit `462ac9e`) 에서 이미 작성돼 있었고, Terraform IaC 도 이미 `cloud_run.tf`/`worker.tf`/`memorystore.tf` 에 선언돼 있었다. "코드 레벨" 로는 끝난 상태.

### 실제

첫 실행에서 `timeout` 으로 실패. 워커 풀 스케일은 0 에서 움직이지 않음. 로그는 uvicorn access 로그만 있고 앱 로거 출력이 Cloud Logging 에 전혀 안 보임. 이 지점부터 **표면 증상이 근본 원인을 감추는 3 개 레이어가 한꺼번에 겹쳐 있었음**.

---

## 2. 병목이 생긴 세 지점 (표면 → 깊이 순)

| # | 레이어 | 표면 증상 | 실제 원인 |
|---|--------|-----------|-----------|
| 1 | **Runtime state** | Worker pool 이 scale 안 됨 (`manual_instance_count=0` 유지) | API 서버가 수면 유발 코드를 가지고 있지 않음 — Cloud Run Admin API `workerPools.patch` 호출이 실행되지 않음 |
| 2 | **Config surface** | (1) 해결 후에도 여전히 wake 로그 없음 | `wake_worker._configured()` 가 세 env var 중 2 개가 없어서 조용히 early-return |
| 3 | **GCP IAM** | (2) 해결 후 wake 가 시도되지만 `PermissionDenied` | Cloud Run Admin API 가 `update_mask` 가 scaling 만이어도 proto3 기본값 때문에 compute default SA 에 actAs 검증 |

각 레이어가 이전 레이어를 해결해야 드러나는 구조라 **한 번에 파악 불가능**. 순차 디버깅만 가능했음.

---

## 3. 기술 스택 적용에서 실제로 겪은 어려움

### 3-1. Cloud Run Worker Pools (ADR-021 §4)

- **`cpu < 1` 거부**: `ee_worker_resources.cpu = "0.5"` 로 설정했더니 apply 가 `Invalid value specified for cpu. Total cpu < 1 is not supported with gen2` 로 거부됨. Worker Pools 는 Cloud Run v2 gen2 실행 환경 + always-allocated CPU 를 강제하므로 최소 shape 는 `cpu=1`. Cloud Run **Service** (v1/v2) 는 0.5 이하도 허용되는 것과 대비되는 차이. → `variables.tf` 에 `cpu = "1"` 로 고정.
- **AUTOMATIC scaling 불가**: `google-cloud-run` Python SDK 0.16.0 의 `WorkerPoolScaling` 에는 `manual_instance_count` 만 writable. `min_instance_count`/`max_instance_count` 는 generated proto 에 아직 없음. 그래서 scaling_mode 를 MANUAL 로 고정하고 wake 는 "count=1 로 patch" 로 구현. Scale-down back to 0 은 자동 안 됨 — Phase 6 현재 상태에서는 `terraform destroy` 나 명시적 patch 가 유일한 방법. 별도 idle watchdog 은 post-Phase-6 TODO.
- **`template.service_account=""` 의 서버측 해석**: proto3 는 unset string 을 ""로 직렬화. Cloud Run Admin API 서버는 이걸 "compute default SA 를 사용하라" 로 해석하고 actAs 검증 대상에 넣음. `update_mask` 가 scaling 에만 걸려 있어도 서버측 validation 은 full proto 를 봄. **결과적으로 API SA 는 compute default SA 에도 `iam.serviceAccountUser` 가 필요**. 공식 문서에 명시 없음 — 실전에서 403 먹어야 발견.

### 3-2. Celery + Memorystore Redis broker

- **VPC peering latency**: Memorystore BASIC 인스턴스 생성에 5분 13초. `terraform apply` 중 가장 긴 단일 작업. 재시도 불가 (같은 이름 재생성엔 쿨다운).
- **Broker URL composition**: `redis://${google_redis_instance.broker.host}:${google_redis_instance.broker.port}/0`. `host` 는 apply 후에만 known. 즉 Memorystore 가 먼저 만들어진 뒤 Worker Pool + API env 를 렌더링하는 의존성이 있음. `depends_on = [google_redis_instance.broker]` 로 강제.
- **Queue 분리 누락**: Worker 는 `workflow_tasks` 큐만 구독하는데, API 가 `send_task` 할 때 `queue=` 명시 안 하면 기본 `celery` 큐로 들어가서 영원히 대기. `fb220de` 에서 이미 수정된 이슈지만 재현 가능성 인지 필요.

### 3-3. Cloud SQL Auth Proxy sidecar

- **Worker 에 빠진 sidecar**: `cloud_run.tf` 의 API 서비스에는 cloudsql-proxy sidecar 가 있지만 `worker.tf` 에는 없었음. `DATABASE_URL` 이 `localhost:5432` 로 구성된 시크릿 값이라 worker 컨테이너가 그대로 사용 → `ConnectionRefusedError: ('127.0.0.1', 5432)` 가 모든 Celery 태스크에서 발생. Worker container 와 proxy container 는 같은 Cloud Run 인스턴스의 pod-like 네트워크 네임스페이스를 공유하므로 localhost 로 proxy 를 부를 수 있다 — 대신 **두 컨테이너가 한 `template` 안에 명시돼 있어야 함**. 해결: `worker.tf` 의 `template` 블록에 `containers { name = "cloudsql-proxy" ... }` 추가.
- **Port 충돌 오해**: 사용자가 "내 PC 에서 5432/5433 이미 사용 중이라 5435 로 통일하자" 제안. 그런데 Cloud Run 컨테이너 내부의 localhost 는 호스트 PC 와 완전히 격리된 네트워크 네임스페이스 — 포트 충돌이 발생할 수 없음. 결국 기본 5432 유지.

### 3-4. Git Bash on Windows — E2E 러너의 호스트 환경 이슈

| 증상 | 원인 | 해결 |
|------|------|------|
| `gcloud: exec: python: not found` | gcloud 의 bash wrapper 가 `python` 을 PATH 에서 찾지만 Windows 에는 `python.exe` 만 있음 | `export CLOUDSDK_PYTHON="/c/Users/user/AppData/Local/Google/Cloud SDK/google-cloud-sdk/platform/bundledpython/python.exe"` |
| `bash: !2026: event not found` | bash history expansion 이 `!` 뒤 토큰을 이전 커맨드로 치환하려 함 (비밀번호에 `!` 포함) | 단일 따옴표로 감싸거나 `set +H` |
| `VERIFY_TOKEN=` 이 빈 문자열 | 한 줄에 `VAR=$(...) curl ...` 로 쓰면 bash 는 환경변수로만 해석하고 다음 줄에서 expand 안 함 | 두 줄로 분리 |
| `WF_ID=tail` | sed greedy match 가 graph body 의 중첩된 `"id":"..."` 를 잡음 | UUID 정규식으로 직접 추출: `grep -oE '[a-f0-9]{8}-[a-f0-9]{4}-...'` |

### 3-5. GCLB 411 on empty POST

`POST /api/v1/workflows/{id}/execute` 는 body 가 실제로 필요 없지만, GCLB 가 Cloud Run 앞단에서 `Content-Length` 없는 POST 를 411 로 reject. 해결: `-d '{}'` 로 빈 JSON 바디 강제. 스크립트에 주석으로 이유 남김.

### 3-6. 배포 이미지 SHA 와 코드 commit SHA 의 정합성

가장 교활했던 문제. 배포돼 있던 API 이미지 태그 `logging-fix-632d8f8` 의 SHA 는 commit `632d8f8` (`fix(ee): Sheets node resolves first-sheet name ...`) 에서 빌드된 것. 그런데 wake_worker 코드는 `f9ecbda` (ADR-021 Phase 5) 에서 추가됨. `git merge-base --is-ancestor 632d8f8 f9ecbda` 결과 True — **즉 배포된 이미지는 wake 코드 이전 시점의 빌드**. 저장소의 현재 코드를 보면 wake 로직이 있는데, 실제 런타임에는 없음.

이걸 발견한 단서:
- `_configured()` 체크 + env var 주입 후에도 wake 로그 전무
- Admin API 에러 로그도 없음 (wake 자체가 호출되지 않으면 except 도 안 탐)
- 결정적 증거: `gcloud run services describe ... --format='value(...image)'` 로 이미지 태그 확인 → git 히스토리와 대조

해결: 현재 HEAD 에서 rebuild → 태그 `phase6-wake-462ac9e` 로 push → `gcloud run deploy --image=...` → revision `00010-hlv` 생성.

rebuild 하다가 파생 문제:

- **Dockerfile 에 Execution_Engine 설치 누락**: `API_Server/pyproject.toml` 은 `auto-workflow-execution-engine` 을 inline-mode 스톱갭 dep 으로 선언 (ADR-021 §5) 하는데, `API_Server/Dockerfile` 은 `./Database` + `./API_Server` 만 pip install 함. PyPI 에는 당연히 이 패키지가 없으므로 빌드 중 `No matching distribution found for auto-workflow-execution-engine` 로 실패. 이전 배포된 이미지 `logging-fix-632d8f8` 는 이 dep 이 `pyproject.toml` 에 추가되기 전 빌드된 거라서 이 gap 이 프로덕션에는 안 드러났음 — **rebuild 를 해야만 드러나는 잠복 버그**. 해결: `COPY Execution_Engine/` + `pip install ./Execution_Engine` 을 Database 와 API_Server 사이에 추가 (PR #98).

---

## 4. 디버깅 로그 (시간순 요약)

```
t+0    docker build (EE) + push → OK
       terraform apply → Memorystore 5m13s, Worker Pool fail: cpu<1
       → variables.tf cpu="1" 로 수정, 재적용 OK
t+15   API 에 env 주입 (CELERY_BROKER_URL, WORKER_POOL_NAME)
       → /execute 호출, status=queued 유지, 워커 풀 scale 0
t+20   gcloud alpha run worker-pools update --instances=1 로 수동 깨움
       → 태스크 즉시 픽업, 그러나 'left_field' KeyError (condition 노드 스키마 불일치)
t+25   WF 재생성 (left_field/right_value/operator 스키마)
       → 3/3 success! 하지만 woken_log_count=0
t+30   [의문] "3회 성공인데 wake 로그 없음" 조사 시작
       → 로그는 수동 scale 덕분에 원래부터 wake 없이 동작했던 것
t+35   Compaction 이후 세션 재개
       API 이미지 SHA (632d8f8) vs f9ecbda 대조 → 이미지가 오래됨 발견
       Dockerfile rebuild 시도 → auto-workflow-execution-engine 없음 에러
       → COPY Execution_Engine + install 추가, rebuild + push (phase6-wake-462ac9e)
t+50   새 이미지 배포 (revision 00010-hlv), 풀 scale→0, E2E 재실행
       여전히 wake 로그 없음. Status=queued 유지
t+55   API env var 덤프 → GCP_PROJECT_ID / GCP_REGION 누락 확인
       → 두 env 주입, redeploy
t+60   E2E 재실행 → 여전히 status=queued, 풀 0 그대로
       로그 필터를 severity>=ERROR 로 넓힘
       → PermissionDenied: iam.serviceaccounts.actAs on
          1038450396751-compute@developer.gserviceaccount.com
       Wake 는 firing, Admin API 단에서 거부당하는 중
t+65   API SA 에 roles/iam.serviceAccountUser 두 개 부여
         - on EE SA
         - on compute default SA
       풀 scale→0, E2E 재실행
       → 3/3 success, woken_log_count=1 (exact), throttle 검증 완료
t+70   IaC 에 인코딩:
         - cloud_run.tf: GCP_PROJECT_ID + GCP_REGION env
         - worker.tf: actAs IAM × 2, cloudsql-proxy sidecar
         - variables.tf: cpu=1
       terraform plan 확인 후 apply → 0 add / 2 change / 0 destroy
       (2 change 는 provider cosmetic drift, 기능 영향 없음)
```

---

## 5. 최종 해결 방법 (IaC 에 인코딩)

### `infra/terraform/cloud_run.tf`

```hcl
env {
  name  = "GCP_PROJECT_ID"
  value = var.project_id
}
env {
  name  = "GCP_REGION"
  value = var.region
}
```

세 wake env var (`WORKER_POOL_NAME` 은 이미 있었음) 가 전부 있어야 `wake_worker._configured()` 가 True 반환.

### `infra/terraform/worker.tf`

```hcl
# cloudsql-proxy sidecar 추가 (worker container 옆에)
containers {
  name  = "cloudsql-proxy"
  image = var.cloudsql_proxy_image
  args = ["--private-ip", "--structured-logs", "--port=5432",
          google_sql_database_instance.main.connection_name]
  resources { limits = { cpu = "1", memory = "256Mi" } }
}

# actAs IAM bindings (both required)
resource "google_service_account_iam_member" "api_actas_ee_runtime" {
  service_account_id = google_service_account.ee_runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.api.email}"
}

data "google_project" "current" { project_id = var.project_id }

resource "google_service_account_iam_member" "api_actas_compute_default" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${data.google_project.current.number}-compute@developer.gserviceaccount.com"
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.api.email}"
}
```

### `API_Server/Dockerfile`

```dockerfile
COPY Database/ ./Database/
COPY Execution_Engine/ ./Execution_Engine/    # 추가
COPY API_Server/ ./API_Server/

RUN pip install --no-cache-dir ./Database \
    && pip install --no-cache-dir ./Execution_Engine \   # 추가
    && pip install --no-cache-dir ./API_Server
```

### `infra/scripts/run_e2e_phase21.sh`

- `-d '{}'` 로 empty POST 시 GCLB 411 회피
- Python 미의존 파싱 (UUID regex + sed)
- execution_id 가 graph body 내 다른 `"id"` 필드와 충돌하지 않도록 grep-first-UUID 전략

---

## 6. 회고 — 다음에 같은 문제 재발 방지

### 6-1. Silent no-op 패턴은 디버깅 지옥

`wake_worker._configured()` 는 "환경변수 없으면 조용히 return" — 로컬/CI 에서는 옳은 동작. 하지만 **배포 환경에서 이 체크가 실패하면 로그가 전혀 없어서 "왜 안 돎?" 을 답할 수 없다**. 같은 패턴이 다른 곳에도 있으면 최소한 DEBUG 레벨로는 "X 가 없어서 스킵" 이라도 찍어야 한다.

### 6-2. 코드와 이미지의 SHA 정합성을 전제로 두지 말 것

저장소의 현재 코드 상태와 배포된 이미지의 빌드 시점은 **완전히 독립적**. Phase N 의 코드가 머지됐다고 해서 배포된 이미지에 그 코드가 있는 건 아니다. 배포 이미지 태그에 git sha 를 포함하는 컨벤션 (`<feature>-<sha7>`) 은 바로 이걸 감사할 수 있게 해주는 유일한 실용 메커니즘 — `gcloud run services describe | grep image` 로 SHA 뽑아서 `git log` 로 비교하면 10초면 검증 가능.

### 6-3. GCP proto3 quirk 는 문서로 안 남아 있음

`workerPools.patch` 의 compute-default SA actAs 검증은 공식 문서에 없다. Python SDK 가 partial WorkerPool 을 serialize 할 때 proto3 기본값 때문에 생기는 **서버측 해석 이슈**. 같은 함정이 다른 Cloud Run Admin API 호출에도 있을 가능성이 크므로, 다음에 "update_mask 가 좁은데 왜 unrelated SA 에 PermissionDenied 가?" 라는 증상을 보면 이 패턴부터 의심.

### 6-4. 3-layer 를 한 번에 못 보는 구조

레이어가 외부에서 안쪽으로 순차적으로만 드러남 — 이미지를 고쳐야 env var 문제가 드러나고, env var 를 고쳐야 IAM 문제가 드러남. 각 레이어에서 "이제 다 됐다" 로 보이다가 다음 레이어로 막힘. **Phase 6 의 교훈**: "E2E 는 한 번 돌려보는 게 아니라, 각 레이어 해결 후마다 돌려보는 것" — 이게 빨리 수렴하는 길.

### 6-5. IaC 에 반영된 것들의 검증 방법

최종 상태는:
```
terraform plan -var-file=environments/staging.tfvars
→ 0 to add, 2 to change (provider cosmetic), 0 to destroy
```

즉 **어떤 클린 환경에서 apply 해도 같은 결과가 재현**. 이전에는 apply 후에 3 개의 수동 gcloud 명령이 필요했음 (env 주입, IAM 부여 × 2). 지금은 한 번의 apply 로 완결.

---

## 7. 관련 PR / 커밋

| 이슈 | PR | 커밋 |
|------|----|----|
| infra IaC 완결 (env vars, IAM × 2, sidecar, cpu=1, runner fixes) | [#97](https://github.com/dhwang0803-glitch/auto_workflow_demo/pull/97) | `30bf307` |
| Dockerfile Execution_Engine 설치 | [#98](https://github.com/dhwang0803-glitch/auto_workflow_demo/pull/98) | `778f998` |
| (예정) ADR-021 `Update (2026-04-20)` — actAs 의존성 | docs 브랜치 | — |

## 8. Follow-up 항목

- [ ] `wake_worker.py` 가 full `template.service_account` 를 명시적으로 보내도록 수정 → compute default SA 의 actAs 바인딩 제거 가능 (blast radius 축소)
- [ ] Idle scale-down watchdog (Cloud Scheduler + Cloud Functions, 또는 컨테이너측 self-terminate) — 현재는 MANUAL scaling 특성상 자동 0 복귀 없음
- [ ] ADR-021 `Update (2026-04-20)` 섹션 — 3-layer wake path 요구사항 + actAs quirk
- [ ] `.github/workflows/` 의 release 파이프라인에서 이미지 태그 - git sha 정합성 체크 (이미지가 빌드된 commit SHA 를 revision label 에 심기)
