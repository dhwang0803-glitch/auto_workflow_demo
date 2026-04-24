# RUNBOOK — AI_Agent Modal 배포

> 2026-04-24 피벗 — Cloud Run GPU / GCE L4 경로는 폐기 (GCP GPU 쿼터·capacity 누적 차단). AI_Agent 는 Modal L4 에서 운영.
>
> 참조: auto-memory `project_agent_modal_pivot.md`, `reference_modal_pitfalls.md`.

## 사전 체크

- [ ] Modal 계정 + 토큰 (`pip install modal && modal token new`)
- [ ] HuggingFace `HF_TOKEN` (Gemma 4 라이선스 수락 후 read 토큰)
- [ ] GCP `autoworkflowdemo` 프로젝트 인증 (bearer secret 읽기용)
- [ ] Python 환경: `modal` 설치된 venv (Windows 면 `PYTHONUTF8=1` 필수)

## 1. GCP-side 부트스트랩 (infra 브랜치)

`infra/terraform/ai_agent.tf` 가 만드는 건 bearer secret 1건뿐. 메인 인프라 apply 의 일부로 자동 생성됨:

```bash
cd infra/terraform
terraform plan  -var-file=environments/staging.tfvars
terraform apply -var-file=environments/staging.tfvars
```

생성: `agent-bearer-token-staging` Secret + `api_sa_bearer_token` IAM (API_Server SA 가 bearer 읽기). 출력 `agent_bearer_token_secret_id` 확인.

## 2. Modal Secrets 등록 (1회)

```bash
# bearer token — GCP Secret 과 동기화
TOKEN=$(gcloud secrets versions access latest \
  --secret=agent-bearer-token-staging --project=autoworkflowdemo)
modal secret create agent-bearer-token AGENT_BEARER_TOKEN=$TOKEN

# HuggingFace read token (rate-limit 회피)
modal secret create huggingface-token HF_TOKEN=hf_...
```

## 3. Modal Volume 모델 다운로드 (1회)

```bash
PYTHONUTF8=1 modal run AI_Agent/scripts/modal_app.py::download_model
```

첫 실행은 ~30-40min (이미지 빌드 ~80min 의 경우 첫 빌드) + HF 다운로드 16.9 GiB. 이후 cold start 에서 Volume 마운트만으로 즉시 접근.

## 4. Modal deploy

```bash
PYTHONUTF8=1 modal deploy AI_Agent/scripts/modal_app.py
```

출력에 endpoint URL (`https://<user>--auto-workflow-agent-agentservice-fastapi.modal.run`) 표시. 대시보드: https://modal.com/apps/<user>/main/deployed/auto-workflow-agent

## 5. Smoke test

```bash
URL="https://<user>--auto-workflow-agent-agentservice-fastapi.modal.run"
TOKEN=$(gcloud secrets versions access latest \
  --secret=agent-bearer-token-staging --project=autoworkflowdemo)

# health — bearer 불필요
curl -sS -m 300 "$URL/v1/health"
# 기대: {"status":"ok","backend":"llamacpp"}

# complete — bearer 필수
curl -sS -m 300 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"system":"You are concise.","user_message":"Say hi.","max_tokens":32}' \
  "$URL/v1/complete"

# bearer 무 → 401
curl -sS -m 30 -w "\n[HTTP %{http_code}]\n" \
  -H "Content-Type: application/json" \
  -d '{"system":"hi","user_message":"hi","max_tokens":16}' \
  "$URL/v1/complete"
```

첫 호출은 cold start 1-3min (image pull + volume mount + model mmap). 이후 warm.

## 6. 롤백 / 정리

```bash
modal app stop auto-workflow-agent
modal volume delete agent-models  # 주의: 다음 deploy 시 download_model 재실행 (16.9 GiB 재다운로드)
modal secret delete agent-bearer-token
modal secret delete huggingface-token
```

GCP-side 는 메인 인프라 일부라 별도 destroy 없음 (`terraform destroy` 는 전체 staging 정리 시).

## 7. 흔한 실패 패턴

| 증상 | 원인 | 조치 |
|---|---|---|
| `UnicodeDecodeError: 'cp949'` on Windows | modal CLI 가 Dockerfile UTF-8 을 cp949 로 읽음 | `PYTHONUTF8=1` prefix 필수 |
| `ERROR: model not found at /vol/...` + Runner failed | Dockerfile ENTRYPOINT 가 container 부팅 차단 | modal_app.py 의 `.dockerfile_commands(["ENTRYPOINT []"])` 확인 |
| `libgomp.so.1: cannot open shared object file` | CUDA runtime 이미지에 OpenMP 런타임 없음 | modal_app.py 의 `.apt_install("libgomp1")` 확인 |
| `unknown model architecture: 'gemma4'` | llama.cpp 가 Gemma 4 지원 전 빌드 | Dockerfile `LLAMA_CPP_REF=b8860+` 확인 (memory `reference_llamacpp_gemma4_minver`) |
| Multi-stage cache 가 stale binary 반환 | Dockerfile ARG 변경 후 Modal 캐시 quirk | modal_app.py 의 `force_build=True` 한 번 켜고 deploy, 성공 후 제거 |
| cold start 가 매번 3분+ | 이미지 pull 이 GPU 노드마다 발생 (~5GB) | scaledown_window 값 늘리거나 min_containers=1 (비용↑) |

## 8. 비용 관리

- L4 in-use 과금 ~$0.59/hr (per-second)
- Modal Volume 16.9 GiB × $0.15/GB·mo ≈ $2.5/mo
- scaledown_window=300s (5분 idle 후 종료). 단발 요청만이면 ≈ 5min/call
- 예상 월간: 해커톤 기간 (~30hr 사용) ≈ **$20-30**

## 9. 다음 단계 (본 RUNBOOK 범위 외)

- **API_Server**: `AI_AGENT_URL` env + `AIAgentHTTPBackend` 이 bearer 자동 부착. Cloud Run env 에 Secret Manager 참조로 주입 (`infra/terraform/cloud_run.tf`).
- **Frontend**: `/api/v1/ai/compose` 를 통해 E2E 검증.
