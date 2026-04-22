# AI_Agent container — FastAPI + llama.cpp server in one Cloud Run GPU unit.
#
# Build from repo root:
#   docker build -f AI_Agent/Dockerfile -t auto-workflow-ai-agent .
#
# Runtime requires an NVIDIA GPU (L4). Model weights are NOT baked in — mount
# them at $MODEL_PATH (Cloud Run: attach a GCS volume or download at startup).

# ---------- Stage 1: build llama-server with CUDA ----------
FROM nvidia/cuda:12.4.1-devel-ubuntu22.04 AS llama-builder

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential git cmake ca-certificates \
        libcurl4-openssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
# Pin to a known-good tag — bump deliberately. master breaks often enough
# that unpinned builds get painful.
ARG LLAMA_CPP_REF=b6000
RUN git clone --depth=1 --branch "${LLAMA_CPP_REF}" https://github.com/ggerganov/llama.cpp.git

WORKDIR /build/llama.cpp
RUN cmake -B build \
        -DGGML_CUDA=ON \
        -DLLAMA_CURL=ON \
        -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build --config Release --target llama-server -j"$(nproc)"


# ---------- Stage 2: runtime (CUDA + Python + app) ----------
# Single runtime stage: installing Python in the CUDA image keeps the venv's
# shebang and shared-lib paths consistent (copying a Debian-built venv onto
# Ubuntu breaks both).
FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04 AS runtime

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.11 python3.11-venv python3-pip \
        libcurl4 ca-certificates \
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

COPY --from=llama-builder /build/llama.cpp/build/bin/llama-server /usr/local/bin/llama-server
# Ship required shared libs (ggml backend .so) alongside the binary.
COPY --from=llama-builder /build/llama.cpp/build/bin/*.so /usr/local/lib/
RUN ldconfig

COPY AI_Agent/scripts/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENV PORT=8100 \
    LLM_BACKEND=llamacpp

WORKDIR /app
USER appuser

EXPOSE 8100

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
