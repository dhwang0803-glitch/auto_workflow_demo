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

import os
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.agents.policy_extract_agent import run_policy_extract_agent
from app.backends.protocols import EmbeddingBackend, LLMBackend
from app.config import Settings
from app.container import AIAgentContainer
from app.dependencies import get_backend, get_settings
from app.models.agents import (
    AgentTrace,
    PolicyExtractReflectiveRequest,
    PolicyExtractReflectiveResponse,
)
from app.models.domain import DomainClassification, DomainClassifyRequest
from app.models.http import CompleteRequest, CompleteResponse, HealthResponse
from app.models.skills import (
    AnswersToSkillRequest,
    AnswerToSkillRequest,
    GapAnalysis,
    GapAnalyzeRequest,
    PolicyExtractRequest,
    PolicyExtractResponse,
    SkillDraft,
)
from app.services.domain_classifier import (
    ClassifierParseError,
    classify_domain,
)
from app.services.policy_extract import (
    PolicyExtractParseError,
    extract_policies,
)
from app.services.skill_bootstrap import (
    SkillBootstrapParseError,
    analyze_gaps,
    answer_to_skill,
    answers_to_skill,
)

# Paths exempt from bearer auth even when AGENT_BEARER_TOKEN is set. /v1/health
# stays open so external monitors / Modal cold-start probes don't need the secret.
_PUBLIC_PATHS = frozenset({"/v1/health"})


def create_app(
    *,
    backend_override: LLMBackend | None = None,
    embedding_override: EmbeddingBackend | None = None,
) -> FastAPI:
    # Eager init so ASGITransport-based tests (which skip lifespan) still see
    # app.state. Backends that hold resources (llamacpp httpx pool) implement
    # aclose(), invoked from the lifespan block below.
    settings = Settings()
    container = AIAgentContainer(
        settings,
        backend_override=backend_override,
        embedding_override=embedding_override,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await container.backend.aclose()
            await container.embedding.aclose()

    app = FastAPI(title="AI_Agent", lifespan=lifespan)
    app.state.settings = settings
    app.state.backend = container.backend
    app.state.embedding = container.embedding

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
            images=payload.images,
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
                images=payload.images,
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

    @app.post("/v1/skills/answers_to_skill", response_model=SkillDraft)
    async def skills_answers_to_skill(
        payload: AnswersToSkillRequest,
        backend: LLMBackend = Depends(get_backend),
    ) -> SkillDraft:
        """Batch — N (parameter, answer) pairs for ONE policy → 1 SkillDraft.

        Replaces the old answer_to_skill (1 Q+A → 1 skill) which fragmented
        a single policy across multiple skills. The legacy endpoint stays
        live until API_Server PR #143 cuts over.
        """
        try:
            return await answers_to_skill(
                backend,
                payload.domain,
                payload.policy_id,
                payload.answers,
            )
        except SkillBootstrapParseError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except ValueError as exc:
            # Unknown policy_id OR unknown parameter name — caller bug.
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/skills/answer_to_skill", response_model=SkillDraft)
    async def skills_answer_to_skill(
        payload: AnswerToSkillRequest,
        backend: LLMBackend = Depends(get_backend),
    ) -> SkillDraft:
        """Legacy single-shot shape (1 Q+A → 1 SkillDraft).

        Routes through the batch path with a one-element list. Kept for
        the W2-7 API_Server contract until PR #143 ships.
        """
        try:
            return await answer_to_skill(
                backend,
                payload.domain,
                payload.policy_id,
                payload.question,
                payload.answer,
            )
        except SkillBootstrapParseError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/policy/extract", response_model=PolicyExtractResponse)
    async def policy_extract(
        payload: PolicyExtractRequest,
        backend: LLMBackend = Depends(get_backend),
    ) -> PolicyExtractResponse:
        """Extract zero+ skill candidates from one parsed document chunk
        (PLAN_12 W3-4 — docs path of skill bootstrap).

        Empty `candidates` is normal — chunks describing org structure /
        history / contact info should produce no policies. Vague chunks
        produce candidates with needs_clarification=true; the review UI
        decides whether to drop, ask, or accept.
        """
        try:
            drafts = await extract_policies(
                backend,
                payload.chunk,
                payload.domain,
                images=payload.images,
            )
        except PolicyExtractParseError as exc:
            # 502 — same convention as the other LLM-backed endpoints:
            # an upstream parse failure is the model's fault, not the
            # caller's, and API_Server can decide to retry or surface
            # the error to the user. Raw payload (truncated) is attached
            # so callers can see what the model emitted before the parse
            # broke — useful for incident response without hopping into
            # Modal logs.
            detail = {
                "error": str(exc),
                "raw_len": len(exc.raw),
                "raw": exc.raw[:1500],
            }
            raise HTTPException(status_code=502, detail=detail) from exc
        return PolicyExtractResponse(candidates=drafts)

    @app.post(
        "/v1/policy/extract_reflective",
        response_model=PolicyExtractReflectiveResponse,
    )
    async def policy_extract_reflective(
        payload: PolicyExtractReflectiveRequest,
        backend: LLMBackend = Depends(get_backend),
    ) -> PolicyExtractReflectiveResponse:
        """Closed-loop reflective extraction (PLAN_13).

        Wraps the same per-chunk extraction as /v1/policy/extract in
        the langgraph agent: extract → self_eval → (reflect → extract)*
        up to `max_iter` times. Response carries the FINAL iteration's
        candidates plus the full agent_trace so operators can audit
        each pass without an external trace UI.

        Co-exists with /v1/policy/extract — callers running A/B
        regression measurement (PR-D's smoke) hit both with the same
        chunk and compare recall + latency.
        """
        # Same backend powers both extraction and the LLM judge — both
        # paths go through the same Modal Gemma deployment, so a second
        # model would just add a different cost profile without buying
        # us anything. PR-α/β onward (ADR-024) routes everything through
        # the ReAct agent loop; the agent itself decides when to call
        # extract vs evaluate via `<tool_call>` blocks.
        tracing_on = (
            os.environ.get("LANGCHAIN_TRACING_V2", "").strip().lower()
            in {"1", "true", "yes", "on"}
            and bool(os.environ.get("LANGCHAIN_API_KEY", "").strip())
        )
        # Pre-mint the LangSmith run id so the wire response can carry
        # it before the trace is fully ingested. Older PRs threaded this
        # through langgraph's `config={"run_id": ...}`; the agent_loop
        # uses `@traceable` and inherits the parent run id from the
        # active LangSmith RunTree. We surface the bare UUID; clients
        # paste it into the LangSmith UI search to find the run.
        run_id = str(uuid.uuid4())

        try:
            iterations, terminated, reason, final_candidates = (
                await run_policy_extract_agent(
                    backend,
                    chunk=payload.chunk,
                    domain=payload.domain,
                    images=payload.images,
                    max_iter=payload.max_iter,
                    judge_backend=backend,
                )
            )
        except PolicyExtractParseError as exc:
            # Same 502 envelope as /v1/policy/extract: a parse failure
            # on the FIRST extraction propagates up. Later-iter parse
            # failures get folded into the agent trace as a tool error
            # obs (the agent can recover or finish), matching the
            # pre-refactor behavior closely enough for callers.
            detail = {
                "error": str(exc),
                "raw_len": len(exc.raw),
                "raw": exc.raw[:1500],
            }
            raise HTTPException(status_code=502, detail=detail) from exc

        # The canonical LangSmith run URL needs the org_id +
        # project_id, neither of which the agent server carries — we
        # surface only the UUID and let the client (smoke script /
        # Frontend) paste it into the LangSmith UI's search. Setting
        # this to None when tracing is off makes the absence of a
        # trace explicit on the wire.
        langsmith_run_id: str | None = run_id if tracing_on else None

        return PolicyExtractReflectiveResponse(
            candidates=final_candidates,
            agent_trace=AgentTrace(
                iterations=iterations,
                terminated=terminated,
                reason=reason,
            ),
            langsmith_run_id=langsmith_run_id,
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
