"""StubEmbeddingBackend — deterministic, dependency-free embedding backend.

Used by local dev / pytest when `EMBEDDING_BACKEND=stub` (the default
outside Modal). Generates a 1024-dim L2-normalized vector per input via
SHA-256 expansion of the text. Same input always yields the same vector,
so retrieval tests are reproducible without sentence-transformers / torch
in the dev image.

NOT semantically meaningful — distance between unrelated texts is
essentially uniform. The stub exists to wire up the
`policy_extractions.embedding` column and exercise the full upload →
chunk → embed → store path; semantic similarity tests must run against
the live `BgeM3EmbeddingBackend` on Modal.
"""
from __future__ import annotations

import hashlib
import math
import struct


class StubEmbeddingBackend:
    """Hash-derived embedding stub matching BGE-M3's 1024-dim shape."""

    dimension: int = 1024

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        # Each SHA-256 digest is 32 bytes = 8 float32 lanes. We chain
        # digests with an incrementing nonce until we have `dimension`
        # floats. The text byte content is the only entropy source, so
        # equal inputs produce equal vectors — required for test
        # determinism across runs.
        target_floats = self.dimension
        floats: list[float] = []
        nonce = 0
        seed = text.encode("utf-8")
        while len(floats) < target_floats:
            digest = hashlib.sha256(seed + nonce.to_bytes(4, "big")).digest()
            # 32 bytes / 4 bytes-per-float32 = 8 floats per digest.
            for i in range(0, 32, 4):
                # Map [0, 2^32) → [-1.0, 1.0) so vectors look balanced
                # rather than all-positive.
                u32 = struct.unpack(">I", digest[i : i + 4])[0]
                floats.append((u32 / 2_147_483_648.0) - 1.0)
            nonce += 1
        floats = floats[:target_floats]
        # L2-normalize so the stub matches BgeM3's `normalize_embeddings=True`
        # contract — downstream cosine similarity expects unit vectors.
        norm = math.sqrt(sum(x * x for x in floats))
        if norm == 0.0:
            # Defensive: empty string + nonce 0 still produces a non-zero
            # digest, so this branch is practically unreachable. Still,
            # avoid div-by-zero.
            return [0.0] * target_floats
        return [x / norm for x in floats]

    async def ready(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None
