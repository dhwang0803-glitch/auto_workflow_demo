"""AI_Agent FastAPI app — LLM service boundary for API_Server.

Exposes low-level LLM endpoints:

- POST `/v1/complete` — non-streaming. Request: system + user_message +
  max_tokens. Response: `{text: "..."}`.
- POST `/v1/stream` — streaming. Same request. Response: chunked
  text/plain; each chunk is raw model text concatenated by the caller.
- GET  `/v1/health` — returns `{status, backend}` for readiness probes.
  Status reflects the backend's `ready()` — for llamacpp this probes the
  llama-server subprocess, so Cloud Run's startup probe waits for model load.

API_Server's `AIAgentHTTPBackend` (app/services/ai_agent_client.py)
consumes these endpoints. The full AI Composer orchestration (prompt
build, parse, rate limit) stays in API_Server — AI_Agent is intentionally
thin in this PR so the boundary is easy to validate.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse, StreamingResponse

from app.backends.protocols import LLMBackend
from app.config import Settings
from app.container import AIAgentContainer
from app.dependencies import get_backend, get_settings
from app.models.http import CompleteRequest, CompleteResponse, HealthResponse


def create_app(*, backend_override: LLMBackend | None = None) -> FastAPI:
    # Eager init so ASGITransport-based tests (which skip lifespan) still see
    # app.state. Backends that hold resources (llamacpp httpx pool) implement
    # aclose(), invoked from the lifespan block below.
    settings = Settings()
    container = AIAgentContainer(settings, backend_override=backend_override)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await container.backend.aclose()

    app = FastAPI(title="AI_Agent", lifespan=lifespan)
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
        backend: LLMBackend = Depends(get_backend),
    ) -> JSONResponse:
        is_ready = await backend.ready()
        body = HealthResponse(
            status="ok" if is_ready else "starting",
            backend=settings.llm_backend,
        )
        # 503 while the underlying model is still loading keeps Cloud Run's
        # startup probe waiting instead of routing traffic too early.
        status_code = 200 if is_ready else 503
        return JSONResponse(body.model_dump(), status_code=status_code)

    return app


app = create_app()
