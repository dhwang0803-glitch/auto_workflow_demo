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

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.backends.protocols import LLMBackend
from app.config import Settings
from app.container import AIAgentContainer
from app.dependencies import get_backend, get_settings
from app.models.domain import DomainClassification, DomainClassifyRequest
from app.models.http import CompleteRequest, CompleteResponse, HealthResponse
from app.models.skills import (
    AnswerToSkillRequest,
    GapAnalysis,
    GapAnalyzeRequest,
    SkillDraft,
)
from app.services.domain_classifier import (
    ClassifierParseError,
    classify_domain,
)
from app.services.skill_bootstrap import (
    SkillBootstrapParseError,
    analyze_gaps,
    answer_to_skill,
)

# Paths exempt from bearer auth even when AGENT_BEARER_TOKEN is set. /v1/health
# stays open so external monitors / Modal cold-start probes don't need the secret.
_PUBLIC_PATHS = frozenset({"/v1/health"})


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

    if settings.agent_bearer_token:
        expected = settings.agent_bearer_token

        @app.middleware("http")
        async def bearer_auth(request: Request, call_next):
            if request.url.path in _PUBLIC_PATHS:
                return await call_next(request)
            auth = request.headers.get("authorization", "")
            if not auth.startswith("Bearer "):
                return JSONResponse({"detail": "missing bearer"}, status_code=401)
            if auth[len("Bearer ") :] != expected:
                return JSONResponse({"detail": "invalid bearer"}, status_code=403)
            return await call_next(request)

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

    @app.post("/v1/domain/classify", response_model=DomainClassification)
    async def domain_classify(
        payload: DomainClassifyRequest,
        backend: LLMBackend = Depends(get_backend),
    ) -> DomainClassification:
        try:
            return await classify_domain(backend, payload.text)
        except ClassifierParseError as exc:
            # 502 — upstream LLM returned a shape we cannot interpret.
            # API_Server can decide whether to fall back to "other" or
            # surface a wizard error to the user.
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/v1/skills/gap_analyze", response_model=GapAnalysis)
    async def skills_gap_analyze(
        payload: GapAnalyzeRequest,
        backend: LLMBackend = Depends(get_backend),
    ) -> GapAnalysis:
        try:
            return await analyze_gaps(
                backend, payload.domain, payload.extracted_skills
            )
        except SkillBootstrapParseError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/v1/skills/answer_to_skill", response_model=SkillDraft)
    async def skills_answer_to_skill(
        payload: AnswerToSkillRequest,
        backend: LLMBackend = Depends(get_backend),
    ) -> SkillDraft:
        try:
            return await answer_to_skill(
                backend,
                payload.domain,
                payload.policy_id,
                payload.question,
                payload.answer,
            )
        except SkillBootstrapParseError as exc:
            # LLM gave us a shape we cannot interpret. Must come BEFORE
            # the ValueError handler since SkillBootstrapParseError
            # inherits from ValueError.
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except ValueError as exc:
            # Unknown policy_id for the given domain — caller bug.
            raise HTTPException(status_code=422, detail=str(exc)) from exc

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
