"""Modal deployment for AI_Agent — Gemma 4 26B-A4B on llama.cpp via L4 GPU.

Pivoted to Modal on 2026-04-24 after GCP exhausted: Cloud Run GPU project quota
unassignable, GCE L4 spot capacity drained across us-central1 zones, GCE
on-demand blocked by GPUS_ALL_REGIONS=0. Modal provides per-second L4 billing
without quota negotiation.

Layout:
- `image`: built from AI_Agent/Dockerfile so llama-server + venv + app match
  the Cloud-Run/GCE path bit-for-bit. First build ~20 min, cached thereafter.
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
MODEL_DIR = "/vol"
MODEL_PATH = f"{MODEL_DIR}/{MODEL_FILE}"
LLAMA_SERVER_PORT = 8080
FASTAPI_PORT = 8100

# Image rebuilt via Dockerfile so llama-server (CUDA 12.4 build) + Python venv
# + AI_Agent app live exactly where the existing tests assume. Modal overrides
# the Dockerfile ENTRYPOINT — we run llama-server from @enter() instead.
image = (
    modal.Image.from_dockerfile(
        path="AI_Agent/Dockerfile",
        context_dir=".",
        # Dockerfile 변경 후 Modal multi-stage 캐시가 stale binary 를 stage 2
        # 로 COPY 하는 사례 발생 시 force_build=True 한 번 켜고 deploy 후 다시
        # 끄세요. 평소엔 False 가 정상 — 매 deploy 80min 빌드 회피.
    )
    # llama-server is built with -fopenmp and dlopens libgomp.so.1 at runtime.
    # The Dockerfile's runtime stage installs only libcurl4 + ca-certificates,
    # so libgomp is missing. Adding here as a Modal layer instead of editing
    # the Dockerfile to preserve the cached llama.cpp build (~80 min).
    .apt_install("libgomp1")
    .pip_install("huggingface_hub>=0.24")
    .env({
        "LLM_BACKEND": "llamacpp",
        "MODEL_PATH": MODEL_PATH,
        "LLAMA_SERVER_URL": f"http://127.0.0.1:{LLAMA_SERVER_PORT}",
        "PORT": str(FASTAPI_PORT),
    })
    # Dockerfile's ENTRYPOINT runs entrypoint.sh which checks MODEL_PATH and
    # exits 1 when missing — that blocks every Modal container start
    # (including the download_model function that's supposed to populate the
    # volume). Modal owns the runner; clear the inherited ENTRYPOINT so the
    # Python entrypoint Modal injects runs cleanly.
    .dockerfile_commands(["ENTRYPOINT []"])
)

model_volume = modal.Volume.from_name("agent-models", create_if_missing=True)
bearer_secret = modal.Secret.from_name("agent-bearer-token")
hf_secret = modal.Secret.from_name("huggingface-token")

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

    if Path(MODEL_PATH).exists():
        size_gb = Path(MODEL_PATH).stat().st_size / 1e9
        print(f"[skip] {MODEL_PATH} present ({size_gb:.1f} GB)")
        return

    Path(MODEL_DIR).mkdir(parents=True, exist_ok=True)
    print(f"downloading {MODEL_REPO}/{MODEL_FILE} → {MODEL_PATH}")
    # Token optional — unsloth GGUF repos are typically public, but HF rate-
    # limits anonymous downloads. Pass HF_TOKEN secret to avoid throttling.
    hf_hub_download(
        repo_id=MODEL_REPO,
        filename=MODEL_FILE,
        local_dir=MODEL_DIR,
        token=os.environ.get("HF_TOKEN") or None,
    )
    model_volume.commit()
    size_gb = Path(MODEL_PATH).stat().st_size / 1e9
    print(f"done — {size_gb:.1f} GB committed to volume")


@app.cls(
    image=image,
    gpu="L4",
    volumes={MODEL_DIR: model_volume},
    secrets=[bearer_secret, hf_secret],
    timeout=600,
    scaledown_window=300,
)
@modal.concurrent(max_inputs=4)
class AgentService:
    @modal.enter()
    def boot(self) -> None:
        import httpx

        if not Path(MODEL_PATH).exists():
            raise FileNotFoundError(
                f"Model missing at {MODEL_PATH}. Run "
                "`modal run AI_Agent/scripts/modal_app.py::download_model` first."
            )

        cmd = [
            "/usr/local/bin/llama-server",
            "--model", MODEL_PATH,
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
