#!/usr/bin/env bash
# Container entrypoint: starts llama-server as a background subprocess, then
# uvicorn in the foreground. The FastAPI `/v1/health` endpoint proxies
# llama-server's readiness so Cloud Run's startup probe waits for the model
# mmap + KV cache to settle.
#
# Env knobs (defaults tuned for L4 GPU):
#   MODEL_PATH           — gguf path (mounted from GCS or baked in)
#   LLAMA_SERVER_PORT    — localhost port for llama-server
#   N_GPU_LAYERS         — 999 = offload all to GPU
#   CTX_SIZE             — context window
#   PORT                 — Cloud Run-provided port for uvicorn

set -euo pipefail

: "${MODEL_PATH:=/models/gemma-4-26B-A4B-it-UD-Q4_K_M.gguf}"
: "${LLAMA_SERVER_HOST:=127.0.0.1}"
: "${LLAMA_SERVER_PORT:=8080}"
: "${N_GPU_LAYERS:=999}"
: "${CTX_SIZE:=8192}"
: "${PORT:=8100}"

# Default LLM_BACKEND for this container is llamacpp — override to stub via
# Cloud Run env for the llama-server-less smoke image.
export LLM_BACKEND="${LLM_BACKEND:-llamacpp}"
export LLAMA_SERVER_URL="${LLAMA_SERVER_URL:-http://${LLAMA_SERVER_HOST}:${LLAMA_SERVER_PORT}}"

if [ "$LLM_BACKEND" = "llamacpp" ]; then
  if [ ! -f "$MODEL_PATH" ]; then
    echo "ERROR: model not found at $MODEL_PATH" >&2
    exit 1
  fi

  llama-server \
    --model "$MODEL_PATH" \
    --host "$LLAMA_SERVER_HOST" \
    --port "$LLAMA_SERVER_PORT" \
    --n-gpu-layers "$N_GPU_LAYERS" \
    --ctx-size "$CTX_SIZE" \
    &
  LLAMA_PID=$!

  # Forward SIGTERM/SIGINT so Cloud Run graceful-stop is honored by both
  # processes. `wait -n` returns if either exits — we fail fast then.
  trap 'kill -TERM "$LLAMA_PID" 2>/dev/null || true' TERM INT
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
