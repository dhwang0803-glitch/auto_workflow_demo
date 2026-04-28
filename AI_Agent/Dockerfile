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
# b8860 (April 2026) — first stable cut past the Gemma 4 day-0 PRs (#21326
# parser + #21343 tokenizer). Earlier b6000 fails on `unknown model
# architecture: 'gemma4'` when loading unsloth/gemma-4-26B-A4B-it GGUF.
ARG LLAMA_CPP_REF=b8860
RUN git clone --depth=1 --branch "${LLAMA_CPP_REF}" https://github.com/ggerganov/llama.cpp.git

WORKDIR /build/llama.cpp
# llama.cpp b6000+ links against the CUDA Driver API (cuMemMap/cuMemRelease/…),
# which lives in libcuda.so.1. The CUDA devel image only ships the SONAME-less
# stub `libcuda.so` under /usr/local/cuda/lib64/stubs/. Final-link of
# llama-server fails on transitive resolution of libggml-cuda.so → libcuda.so.1
# unless ld is told where to look. Belt-and-suspenders:
#   1. Create the .so.1 symlink so the SONAME exists.
#   2. Register the stubs dir with ldconfig so it's part of the system search
#      path (covers transitive resolution).
#   3. Pass -Wl,-rpath-link so the executable link step explicitly resolves
#      transitive deps from the stubs dir (covers cmake-managed link).
# Runtime libcuda.so.1 comes from the host NVIDIA driver via
# nvidia-container-runtime / Cloud Run GPU injection — these stubs are
# build-only (no symbols at exec time, but .so.1 SONAME satisfied).
RUN ln -sf /usr/local/cuda/lib64/stubs/libcuda.so /usr/local/cuda/lib64/stubs/libcuda.so.1 \
 && echo "/usr/local/cuda/lib64/stubs" > /etc/ld.so.conf.d/cuda-stubs.conf \
 && ldconfig

RUN cmake -B build \
        -DGGML_CUDA=ON \
        -DLLAMA_CURL=ON \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_EXE_LINKER_FLAGS="-Wl,-rpath-link,/usr/local/cuda/lib64/stubs" \
        -DCMAKE_SHARED_LINKER_FLAGS="-Wl,-rpath-link,/usr/local/cuda/lib64/stubs" \
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
