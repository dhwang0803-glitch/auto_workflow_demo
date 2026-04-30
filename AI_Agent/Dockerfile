# AI_Agent container — FastAPI + llama.cpp server in one Cloud Run GPU unit.
#
# Build from repo root:
#   docker build -f AI_Agent/Dockerfile -t auto-workflow-ai-agent .
#
# Runtime requires an NVIDIA GPU (L4). Model weights are NOT baked in — mount
# them at $MODEL_PATH (Cloud Run: attach a GCS volume or download at startup).
#
# llama-server binary is pulled from the upstream pre-built CUDA image
# (ghcr.io/ggml-org/llama.cpp:server-cuda12-b8967) instead of compiled here.
# The from-source build took ~80 min and got the Modal builder preempted three
# times in a row on 2026-04-30 — see reference_modal_pitfalls.md (#7).

# ---------- Runtime: CUDA + Python 3.11 + app ----------
# Base must match the upstream llama.cpp image (Ubuntu 24.04 + CUDA 12.8) so
# glibc/libstdc++ ABIs line up with the binary copied below — copying a
# 24.04-built llama-server onto a 22.04 runtime breaks at GLIBCXX symbol
# resolution. Python 3.11 is installed via deadsnakes PPA because 24.04 ships
# 3.12 by default; see project_python_311_decision.md.
FROM nvidia/cuda:12.8.1-runtime-ubuntu24.04 AS runtime

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        software-properties-common ca-certificates gnupg \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
        python3.11 python3.11-venv \
        libcurl4 libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash --uid 10001 appuser \
    && python3.11 -m venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY AI_Agent/pyproject.toml /tmp/AI_Agent/pyproject.toml
COPY AI_Agent/app /tmp/AI_Agent/app
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir /tmp/AI_Agent \
    && rm -rf /tmp/AI_Agent

# Pre-built llama-server (CUDA 12, Ubuntu 24.04) from the official image.
# Bump the tag deliberately when picking up llama.cpp fixes — b8967 (April
# 2026) is past the Gemma 4 day-0 PRs (#21326 parser + #21343 tokenizer);
# earlier builds fail on `unknown model architecture: 'gemma4'`.
#
# Install layout matches the upstream image: binary + plugin .so files all
# live in /opt/llama/. ggml's `ggml_backend_load_all()` dlopen-scans the
# binary's directory for libggml-*.so (and *also* uses GGML_BACKEND_PATH).
# Putting the libs anywhere outside the binary's dir — e.g. /usr/local/lib
# with ldconfig — silently boots without the CUDA backend ("no backends are
# loaded"), wastes the L4, and falls back to CPU.
COPY --from=ghcr.io/ggml-org/llama.cpp:server-cuda12-b8967 /app/llama-server /opt/llama/llama-server
COPY --from=ghcr.io/ggml-org/llama.cpp:server-cuda12-b8967 /app/*.so /opt/llama/
RUN ln -sf /opt/llama/llama-server /usr/local/bin/llama-server

COPY AI_Agent/scripts/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENV PORT=8100 \
    LLM_BACKEND=llamacpp \
    GGML_BACKEND_PATH=/opt/llama \
    LD_LIBRARY_PATH=/opt/llama

WORKDIR /app
USER appuser

EXPOSE 8100

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
