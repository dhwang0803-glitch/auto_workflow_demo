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
# Two distinct loaders need to find the .so files:
#   - transitive linking (libllama-common.so.0 etc) — ld.so reads
#     /etc/ld.so.cache, populated by ldconfig.
#   - ggml plugin loader (libggml-cuda.so) — `ggml_backend_load_all()`
#     scans the binary's directory and cwd for libggml-*.so. GGML_BACKEND_PATH
#     env is *not* a directory hint — ggml passes it straight to dlopen, so
#     it must point at a single .so file. Setting it to a directory boots to
#     CPU silently (b8967: `load_backend: failed to load <dir>: Is a directory`).
# Co-locating binary + .so in /usr/local/bin and registering that path with
# ldconfig satisfies both loaders without env-var trickery.
COPY --from=ghcr.io/ggml-org/llama.cpp:server-cuda12-b8967 /app/llama-server /usr/local/bin/llama-server
COPY --from=ghcr.io/ggml-org/llama.cpp:server-cuda12-b8967 /app/*.so /usr/local/bin/
RUN echo "/usr/local/bin" > /etc/ld.so.conf.d/llama.conf && ldconfig

COPY AI_Agent/scripts/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENV PORT=8100 \
    LLM_BACKEND=llamacpp

WORKDIR /app
USER appuser

EXPOSE 8100

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
