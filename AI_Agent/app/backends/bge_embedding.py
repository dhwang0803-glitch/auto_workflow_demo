"""BgeM3EmbeddingBackend — production text→1024-dim embedding via BAAI/bge-m3.

Selected via `EMBEDDING_BACKEND=bge_m3`. Loaded inside the same Modal
container that hosts llama-server (ADR-022 §8.5 colocation: validated by
`scripts/modal_validate_bge_gemma.py`, which measured BGE-M3 + Gemma 4
26B-A4B-it both fitting on a single L4).

The HuggingFace model (~2 GB) is NOT baked into the Docker image — first
`embed()` call downloads it via sentence-transformers' default HF cache
(~2-3 min on a fresh container, instant on subsequent calls within the
container's disk lifetime). For deterministic cold-start latency we eager-
load on `ready()` so /v1/health gates traffic until the model is live.

Why this isn't a hard dep of the AI_Agent package: sentence-transformers
pulls torch (~2 GB) which would bloat local dev installs and CI test
images for code that has no use for it. We keep the import lazy and only
the production Modal image ever pip-installs the heavy deps (see
`scripts/modal_app.py`).
"""
from __future__ import annotations

import asyncio
from threading import Lock


class BgeM3EmbeddingBackend:
    """sentence-transformers BGE-M3 backend, 1024-dim L2-normalized."""

    dimension: int = 1024

    def __init__(
        self,
        *,
        model_name: str = "BAAI/bge-m3",
        device: str | None = None,
    ) -> None:
        # `device` of None lets sentence-transformers auto-detect (cuda
        # if available, else cpu). Tests that need to force cpu pass
        # device="cpu" explicitly.
        self._model_name = model_name
        self._device = device
        self._model = None  # lazy — see _ensure_loaded()
        self._load_lock = Lock()

    def _ensure_loaded(self) -> None:
        """Load the SentenceTransformer model exactly once.

        The lock guards against two concurrent embed() calls each
        triggering a load on the cold container — the second call
        should observe the model the first one finished loading.
        """
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            # Local import: keeps `from app.backends import EmbeddingBackend`
            # working in test envs that don't ship sentence-transformers.
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(
                self._model_name, device=self._device
            )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # SentenceTransformer.encode is sync + CPU/GPU-blocking. Wrap in
        # to_thread so the event loop stays responsive during a batch
        # encode (a 100-chunk PDF takes ~1-2s on L4).
        def _encode() -> list[list[float]]:
            self._ensure_loaded()
            assert self._model is not None  # appease the type checker
            arr = self._model.encode(
                texts,
                normalize_embeddings=True,  # cosine-ready unit vectors
                convert_to_numpy=True,
            )
            # arr.shape == (len(texts), 1024); to plain Python lists so
            # callers don't need numpy.
            return [row.tolist() for row in arr]

        return await asyncio.to_thread(_encode)

    async def ready(self) -> bool:
        # Eager-load so /v1/health stays 503 until the model is in memory.
        # Cloud Run / Modal startup probes wait on this, so we don't
        # accept embed traffic against a half-loaded model.
        try:
            await asyncio.to_thread(self._ensure_loaded)
            return True
        except Exception:
            # Don't crash the health endpoint — return False and let the
            # probe retry. Common causes: HF rate limit, transient disk
            # pressure during model download.
            return False

    async def aclose(self) -> None:
        # SentenceTransformer doesn't expose an explicit shutdown — let
        # GC + CUDA context teardown handle it on container exit.
        self._model = None
