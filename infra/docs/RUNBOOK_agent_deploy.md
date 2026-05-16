# RUNBOOK — AI_Agent Modal deployment

> Pivoted 2026-04-24 — the Cloud Run GPU / GCE L4 paths are retired
> (GCP GPU quota & capacity were repeatedly blocked). AI_Agent runs on
> Modal L4.
>
> References: auto-memory `project_agent_modal_pivot.md`,
> `reference_modal_pitfalls.md`.

## Preflight

- [ ] Modal account + token (`pip install modal && modal token new`)
- [ ] Hugging Face `HF_TOKEN` (read token after accepting the Gemma 4
      license)
- [ ] GCP `autoworkflowdemo` project auth (for reading the bearer
      secret)
- [ ] Python environment: a venv with `modal` installed (on Windows,
      `PYTHONUTF8=1` is required)

## 1. GCP-side bootstrap (infra branch)

`infra/terraform/ai_agent.tf` creates just one bearer secret. It is
generated as part of the main infra apply:

```bash
cd infra/terraform
terraform plan  -var-file=environments/staging.tfvars
terraform apply -var-file=environments/staging.tfvars
```

Creates: the `agent-bearer-token-staging` Secret + the
`api_sa_bearer_token` IAM binding (so the API_Server SA can read the
bearer). Verify the `agent_bearer_token_secret_id` output.

## 2. Register Modal secrets (one-time)

```bash
# bearer token — synced with the GCP secret
TOKEN=$(gcloud secrets versions access latest \
  --secret=agent-bearer-token-staging --project=autoworkflowdemo)
modal secret create agent-bearer-token AGENT_BEARER_TOKEN=$TOKEN

# Hugging Face read token (rate-limit avoidance)
modal secret create huggingface-token HF_TOKEN=hf_...
```

## 3. Download the model to a Modal Volume (one-time)

```bash
PYTHONUTF8=1 modal run AI_Agent/scripts/modal_app.py::download_model
```

The first run takes ~30–40 min (or ~80 min when the image also has to
be built for the first time) plus the 16.9 GiB HF download. After
that, cold starts just mount the volume and use it immediately.

## 4. Modal deploy

```bash
PYTHONUTF8=1 modal deploy AI_Agent/scripts/modal_app.py
```

The output prints the endpoint URL
(`https://<user>--auto-workflow-agent-agentservice-fastapi.modal.run`).
Dashboard:
`https://modal.com/apps/<user>/main/deployed/auto-workflow-agent`.

## 5. Smoke test

```bash
URL="https://<user>--auto-workflow-agent-agentservice-fastapi.modal.run"
TOKEN=$(gcloud secrets versions access latest \
  --secret=agent-bearer-token-staging --project=autoworkflowdemo)

# health — bearer not required
curl -sS -m 300 "$URL/v1/health"
# expected: {"status":"ok","backend":"llamacpp"}

# complete — bearer required
curl -sS -m 300 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"system":"You are concise.","user_message":"Say hi.","max_tokens":32}' \
  "$URL/v1/complete"

# no bearer → 401
curl -sS -m 30 -w "\n[HTTP %{http_code}]\n" \
  -H "Content-Type: application/json" \
  -d '{"system":"hi","user_message":"hi","max_tokens":16}' \
  "$URL/v1/complete"
```

The first call has a 1–3 min cold start (image pull + volume mount +
model mmap). Subsequent calls are warm.

## 6. Rollback / cleanup

```bash
modal app stop auto-workflow-agent
modal volume delete agent-models  # NOTE: next deploy must re-run download_model (16.9 GiB redownload)
modal secret delete agent-bearer-token
modal secret delete huggingface-token
```

The GCP side is part of the main infra apply, so there is no separate
destroy (`terraform destroy` is reserved for tearing down all of
staging).

## 7. Common failure patterns

| Symptom | Cause | Action |
|---------|-------|--------|
| `UnicodeDecodeError: 'cp949'` on Windows | The Modal CLI reads the UTF-8 Dockerfile as cp949 | Prefix with `PYTHONUTF8=1` |
| `ERROR: model not found at /vol/...` + Runner failed | The Dockerfile ENTRYPOINT prevents container boot | Confirm `.dockerfile_commands(["ENTRYPOINT []"])` in modal_app.py |
| `libgomp.so.1: cannot open shared object file` | The CUDA runtime image lacks the OpenMP runtime | Confirm `.apt_install("libgomp1")` in modal_app.py |
| `unknown model architecture: 'gemma4'` | llama.cpp was built before Gemma 4 support landed | Confirm Dockerfile `LLAMA_CPP_REF=b8860+` (memory: `reference_llamacpp_gemma4_minver`) |
| Multi-stage cache returns a stale binary | Modal cache quirk after a Dockerfile ARG change | Set `force_build=True` in modal_app.py for one deploy, then remove it |
| Cold start is always 3+ min | Image pull happens on every GPU node (~5 GB) | Raise `scaledown_window` or set `min_containers=1` (costs more) |

## 8. Cost management

- L4 in-use billing: ~$0.59/hr (per-second)
- Modal Volume: 16.9 GiB × $0.15/GB·mo ≈ $2.5/mo
- `scaledown_window=300s` (terminates 5 min after idle). For one-shot
  requests, that is ~5 min per call.
- Expected monthly cost during the hackathon window (~30 hr usage):
  **$20–30**.

## 9. Follow-ups (out of scope for this RUNBOOK)

- **API_Server**: with the `AI_AGENT_URL` env var,
  `AIAgentHTTPBackend` attaches the bearer automatically. The Cloud
  Run env consumes it via a Secret Manager reference
  (`infra/terraform/cloud_run.tf`).
- **Frontend**: verify end-to-end via `/api/v1/ai/compose`.
