from app.backends.anthropic import AnthropicBackend
from app.backends.protocols import EmbeddingBackend, LLMBackend
from app.backends.stub import StubLLMBackend
from app.backends.stub_embedding import StubEmbeddingBackend

# BgeM3EmbeddingBackend is intentionally NOT re-exported here — its module
# has no top-level sentence-transformers import (lazy in _ensure_loaded),
# but listing it would still tempt callers in test code to import it
# directly. The container is the only legitimate construction site.

__all__ = [
    "LLMBackend",
    "AnthropicBackend",
    "StubLLMBackend",
    "EmbeddingBackend",
    "StubEmbeddingBackend",
]
