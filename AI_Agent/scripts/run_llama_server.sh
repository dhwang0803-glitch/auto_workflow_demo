#!/usr/bin/env bash
# Local dev helper: starts llama-server with the Gemma 4 26B-A4B Q4 model.
#
# Prereqs (once):
#   1. Build or install `llama-server` from github.com/ggerganov/llama.cpp.
#   2. Download the GGUF to $MODEL_PATH. Typical command (requires HF token +
#      Gemma license acceptance):
#        huggingface-cli download unsloth/gemma-4-26B-A4B-it-GGUF \
#          gemma-4-26B-A4B-it-Q4_K_M.gguf \
#          --local-dir "$(dirname "$MODEL_PATH")"
#
# Then start the server (in another terminal):
#   AI_Agent/scripts/run_llama_server.sh
#
# And point AI_Agent at it:
#   LLM_BACKEND=llamacpp LLAMA_SERVER_URL=http://127.0.0.1:8080 \
#     uvicorn app.main:app --port 8100

set -euo pipefail

: "${MODEL_PATH:=${HOME}/.cache/auto_workflow_demo/models/gemma-4-26B-A4B-it-Q4_K_M.gguf}"
: "${LLAMA_SERVER_PORT:=8080}"
: "${LLAMA_SERVER_HOST:=127.0.0.1}"
# 999 = offload all layers to GPU. On L4 (24GB) the 26B-A4B Q4 fits with
# ~6-8GB KV cache headroom.
: "${N_GPU_LAYERS:=999}"
: "${CTX_SIZE:=8192}"

if [ ! -f "$MODEL_PATH" ]; then
  echo "ERROR: model not found at $MODEL_PATH" >&2
  echo "Download via huggingface-cli (see header of this script)." >&2
  exit 1
fi

if ! command -v llama-server >/dev/null 2>&1; then
  echo "ERROR: llama-server not on PATH. Build from github.com/ggerganov/llama.cpp" >&2
  exit 1
fi

exec llama-server \
  --model "$MODEL_PATH" \
  --host "$LLAMA_SERVER_HOST" \
  --port "$LLAMA_SERVER_PORT" \
  --n-gpu-layers "$N_GPU_LAYERS" \
  --ctx-size "$CTX_SIZE"
