"""Modal deployment for AI_Agent — Gemma 4 26B-A4B on llama.cpp via L4 GPU.

Pivoted to Modal on 2026-04-24 after GCP exhausted: Cloud Run GPU project quota
unassignable, GCE L4 spot capacity drained across us-central1 zones, GCE
on-demand blocked by GPUS_ALL_REGIONS=0. Modal provides per-second L4 billing
without quota negotiation.

Layout:
- `image`: built from AI_Agent/Dockerfile. The llama-server binary is COPYed
  from upstream `ghcr.io/ggml-org/llama.cpp:server-cuda12-b8967` (Ubuntu 24.04
  + CUDA 12.8 runtime base — must match the upstream image to keep
  glibc/libstdc++ ABIs aligned). Build is ~5-10 min (no llama.cpp compile).
- `model_volume`: persistent Modal Volume holding the 15.7 GiB GGUF. Populated
  once via `modal run modal_app.py::download_model`.
- `AgentService`: @cls with @enter() boots llama-server subprocess and waits
  on /health, @asgi_app() exposes the FastAPI app over HTTPS.
- Bearer auth: AGENT_BEARER_TOKEN env (Modal Secret) gates /v1/* requests via
  middleware in app/main.py. /v1/health stays public for Modal's probes.

Deploy:
    modal deploy AI_Agent/scripts/modal_app.py

Populate model (one-time):
    modal run AI_Agent/scripts/modal_app.py::download_model
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import modal

APP_NAME = "auto-workflow-agent"
MODEL_REPO = "unsloth/gemma-4-26B-A4B-it-GGUF"
MODEL_FILE = "gemma-4-26B-A4B-it-UD-Q4_K_M.gguf"
MMPROJ_FILE = "mmproj-F16.gguf"
MODEL_DIR = "/vol"
MODEL_PATH = f"{MODEL_DIR}/{MODEL_FILE}"
MMPROJ_PATH = f"{MODEL_DIR}/{MMPROJ_FILE}"
# Per-user personal-skill JSON files live on a separate Volume from the
# 16 GiB model weights so the model Volume stays read-mostly. PR-γ
# (memory `project_personalization_memory_pattern.md`) added the read
# path; PR-I wires the Volume mount + write endpoint that the
# `/v1/personalization/memory/upsert` route persists into.
PERSONAL_MEMORY_DIR = "/personal_memory"
LLAMA_SERVER_PORT = 8080
FASTAPI_PORT = 8100

# Image rebuilt via Dockerfile — single runtime stage now (the from-source
# llama.cpp build was replaced with a COPY --from upstream pre-built image).
# Modal overrides the Dockerfile ENTRYPOINT; llama-server is launched from
# @enter() instead.
image = (
    modal.Image.from_dockerfile(
        path="AI_Agent/Dockerfile",
        context_dir=".",
    )
    .pip_install(
        "huggingface_hub>=0.24",
        # PLAN_12 W3-3 — BGE-M3 embedding backend (1024-dim). Validated as
        # colocated with Gemma 4 on a single L4 by modal_validate_bge_gemma.py
        # (ADR-022 §8.5). Heavy deps (~2 GB torch); bake into the Modal image
        # only — local dev / pytest stays on EMBEDDING_BACKEND=stub.
        "sentence-transformers>=3.0",
        "torch>=2.6",
    )
    .env({
        "LLM_BACKEND": "llamacpp",
        # First /v1/embed call downloads the ~2GB BGE-M3 weights via HF
        # cache. On Modal the container disk persists between warm
        # requests, so the download is paid once per container lifetime.
        # Cold-start optimization (Volume-backed HF cache or eager
        # @enter() preload) is a follow-on if the cold-start pause hurts
        # the demo.
        "EMBEDDING_BACKEND": "bge_m3",
        "MODEL_PATH": MODEL_PATH,
        "LLAMA_SERVER_URL": f"http://127.0.0.1:{LLAMA_SERVER_PORT}",
        "PORT": str(FASTAPI_PORT),
        # Activates the personal-skill memory pool the reflective agent
        # consults via `search_personal_skills`. With this set, the route
        # loader reads `{PERSONAL_MEMORY_DIR}/{user_id}.json`; cold-start
        # (missing file / empty `user_id`) still resolves to an empty
        # pool, preserving the GitLab smoke baseline.
        "PERSONAL_MEMORY_DIR": PERSONAL_MEMORY_DIR,
        # PLAN_13 PR-D — LangSmith tracing on by default in Modal so
        # PLAN_13 demo runs surface in the LangSmith UI. The actual
        # API key is injected via the langsmith_secret below; without
        # the key tracing.py keeps `@traceable` as a no-op so the agent
        # runs identically.
        "LANGCHAIN_TRACING_V2": "true",
        "LANGCHAIN_PROJECT": "auto-workflow-policy-extract",
    })
    # Dockerfile's ENTRYPOINT runs entrypoint.sh which checks MODEL_PATH and
    # exits 1 when missing — that blocks every Modal container start
    # (including the download_model function that's supposed to populate the
    # volume). Modal owns the runner; clear the inherited ENTRYPOINT so the
    # Python entrypoint Modal injects runs cleanly.
    .dockerfile_commands(["ENTRYPOINT []"])
)

model_volume = modal.Volume.from_name("agent-models", create_if_missing=True)
# Separate Volume from `agent-models` so personal-skill writes don't
# co-mingle with the read-mostly model weights and so the JSON files
# survive model_volume rebuilds. The Volume is created lazily on first
# deploy; PR-I's upsert endpoint commits to it after each write.
personal_memory_volume = modal.Volume.from_name(
    "agent-personal-memory", create_if_missing=True
)
bearer_secret = modal.Secret.from_name("agent-bearer-token")
hf_secret = modal.Secret.from_name("huggingface-token")
# PLAN_13 PR-D — LangSmith API key. The Secret is created once
# (manually or via `modal secret create langsmith-api-key
# LANGCHAIN_API_KEY=""` for an empty placeholder); subsequent value
# rotation flows through `scripts/sync-modal-secrets.py` from GCP
# Secret Manager. With an empty key, tracing.py keeps `@traceable`
# as a no-op so the agent runs unchanged; sync the secret + redeploy
# to flip tracing on without code changes.
langsmith_secret = modal.Secret.from_name("langsmith-api-key")

app = modal.App(APP_NAME)


@app.function(
    image=image,
    volumes={MODEL_DIR: model_volume},
    secrets=[hf_secret],
    timeout=3600,
)
def download_model() -> None:
    """One-shot HF → Modal Volume populator. Idempotent.

    Run once after first deploy; subsequent cold starts mmap from the volume
    instantly. Re-running with the file already present is a no-op.
    """
    from huggingface_hub import hf_hub_download

    Path(MODEL_DIR).mkdir(parents=True, exist_ok=True)
    token = os.environ.get("HF_TOKEN") or None

    for filename, target_path in [(MODEL_FILE, MODEL_PATH), (MMPROJ_FILE, MMPROJ_PATH)]:
        if Path(target_path).exists():
            size_gb = Path(target_path).stat().st_size / 1e9
            print(f"[skip] {target_path} present ({size_gb:.1f} GB)")
            continue

        print(f"downloading {MODEL_REPO}/{filename} → {target_path}")
        hf_hub_download(
            repo_id=MODEL_REPO,
            filename=filename,
            local_dir=MODEL_DIR,
            token=token,
        )
        size_gb = Path(target_path).stat().st_size / 1e9
        print(f"done — {size_gb:.1f} GB downloaded")

    model_volume.commit()
    print("volume committed")


@app.cls(
    image=image,
    gpu="L4",
    volumes={
        MODEL_DIR: model_volume,
        PERSONAL_MEMORY_DIR: personal_memory_volume,
    },
    secrets=[bearer_secret, hf_secret, langsmith_secret],
    timeout=600,
    scaledown_window=300,
)
@modal.concurrent(max_inputs=4)
class AgentService:
    @modal.enter()
    def boot(self) -> None:
        import httpx

        for required in (MODEL_PATH, MMPROJ_PATH):
            if not Path(required).exists():
                raise FileNotFoundError(
                    f"Required GGUF missing at {required}. Run "
                    "`modal run AI_Agent/scripts/modal_app.py::download_model` first."
                )

        cmd = [
            "/usr/local/bin/llama-server",
            "--model", MODEL_PATH,
            "--mmproj", MMPROJ_PATH,
            "--host", "127.0.0.1",
            "--port", str(LLAMA_SERVER_PORT),
            "--n-gpu-layers", os.environ.get("N_GPU_LAYERS", "999"),
            "--ctx-size", os.environ.get("CTX_SIZE", "8192"),
        ]
        # stdout merged into container logs; Modal surfaces them in the dashboard.
        self._proc = subprocess.Popen(cmd)

        # Wait until llama-server's /health returns 200 — model mmap takes
        # 30-60s on a fresh boot (warm volume) or longer on first ever boot.
        deadline = time.time() + 180
        last_err: Exception | None = None
        while time.time() < deadline:
            try:
                r = httpx.get(
                    f"http://127.0.0.1:{LLAMA_SERVER_PORT}/health", timeout=2.0
                )
                if r.status_code == 200:
                    print("llama-server ready")
                    return
            except httpx.HTTPError as exc:
                last_err = exc
            time.sleep(1)

        self._proc.terminate()
        raise RuntimeError(f"llama-server not ready in 180s; last error: {last_err}")

    @modal.exit()
    def shutdown(self) -> None:
        if getattr(self, "_proc", None):
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    @modal.asgi_app()
    def fastapi(self):
        # Importing here (not at module load) keeps `modal deploy` lightweight
        # and ensures the app reads env (AGENT_BEARER_TOKEN, etc.) from the
        # container, not the local CLI.
        from app.main import create_app

        return create_app()
