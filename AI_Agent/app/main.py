"""AI_Agent FastAPI app — LLM service boundary for API_Server.

Exposes low-level LLM endpoints:

- POST `/v1/complete` — non-streaming. Request: system + user_message +
  max_tokens. Response: `{text: "..."}`.
- POST `/v1/stream` — streaming. Same request. Response: chunked
  text/plain; each chunk is raw model text concatenated by the caller.
- GET  `/v1/health` — returns `{status, backend}` for readiness probes.

API_Server's `AIAgentHTTPBackend` (app/services/ai_agent_client.py)
consumes these endpoints. The full AI Composer orchestration (prompt
build, parse, rate limit) stays in API_Server — AI_Agent is intentionally
thin in this PR so the boundary is easy to validate.
"""
from __future__ import annotations

from typing import AsyncIterator

from fastapi import Depends, FastAPI
from fastapi.responses import StreamingResponse

from app.backends.protocols import LLMBackend
from app.config import Settings
from app.container import AIAgentContainer
from app.dependencies import get_backend, get_settings
from app.models.http import CompleteRequest, CompleteResponse, HealthResponse


def create_app(*, backend_override: LLMBackend | None = None) -> FastAPI:
    # Eager init (not lifespan): Settings + backend have no async cleanup,
    # and ASGITransport-based tests don't run lifespan events. Putting state
    # here keeps test setup simple. When future resources need async cleanup
    # (e.g. httpx AsyncClient pool for llama-server) add a lifespan block.
    settings = Settings()
    container = AIAgentContainer(settings, backend_override=backend_override)
    app = FastAPI(title="AI_Agent")
    app.state.settings = settings
    app.state.backend = container.backend

    @app.post("/v1/complete", response_model=CompleteResponse)
    async def complete(
        payload: CompleteRequest,
        backend: LLMBackend = Depends(get_backend),
    ) -> CompleteResponse:
        text = await backend.complete(
            system=payload.system,
            user_message=payload.user_message,
            max_tokens=payload.max_tokens,
        )
        return CompleteResponse(text=text)

    @app.post("/v1/stream")
    async def stream_tokens(
        payload: CompleteRequest,
        backend: LLMBackend = Depends(get_backend),
    ) -> StreamingResponse:
        async def _iter() -> AsyncIterator[bytes]:
            async for chunk in backend.stream(
                system=payload.system,
                user_message=payload.user_message,
                max_tokens=payload.max_tokens,
            ):
                yield chunk.encode("utf-8")

        return StreamingResponse(
            _iter(),
            media_type="text/plain",
            headers={
                # Proxies (Cloud Run, nginx) will otherwise buffer the whole
                # stream before delivering.
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/v1/health", response_model=HealthResponse)
    async def health(
        settings: Settings = Depends(get_settings),
    ) -> HealthResponse:
        return HealthResponse(status="ok", backend=settings.llm_backend)

    return app


app = create_app()
