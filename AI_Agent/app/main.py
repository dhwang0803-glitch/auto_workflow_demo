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
from app.dependencies import get_backend, get_embedding_backend, get_settings
from app.models.agents import (
    AgentTrace,
    PolicyExtractReflectiveRequest,
    PolicyExtractReflectiveResponse,
)
from app.models.domain import DomainClassification, DomainClassifyRequest
from app.models.http import CompleteRequest, CompleteResponse, HealthResponse
from app.models.personalization import (
    PersonalMemoryUpsertRequest,
    PersonalMemoryUpsertResponse,
    PersonalizationExtractRequest,
    PersonalizationExtractResponse,
)
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
from app.services.industry_baselines import IndustryBaselinePool
from app.agents.tracing import traceable
from app.services.personal_memory import (
    PersonalMemoryPool,
    PersonalMemoryWriteError,
    PersonalSkillEntry,
    upsert_personal_skill,
)
from app.services.personalization_service import (
    extract_personalization_from_diff,
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

    # PR-M — wrap the LLM call in a `@traceable` helper so LangSmith
    # records the system + user_message + response per call. The
    # decorator is a no-op when LangSmith is off (`tracing.py`), so the
    # signature reads identically in tests; in production runs it
    # surfaces the system prompt that PR-K/L now mix workspace +
    # personal skills into, which is the only practical way to verify
    # live "the system already knows" injection without a roundtrip
    # through model output.
    @traceable(run_type="llm", name="ai_complete")
    async def _traced_complete(
        *,
        backend: LLMBackend,
        system: str,
        user_message: str,
        max_tokens: int,
        images: list[str] | None,
    ) -> str:
        return await backend.complete(
            system=system,
            user_message=user_message,
            max_tokens=max_tokens,
            images=images,
        )

    @app.post("/v1/complete", response_model=CompleteResponse)
    async def complete(
        payload: CompleteRequest,
        backend: LLMBackend = Depends(get_backend),
    ) -> CompleteResponse:
        text = await _traced_complete(
            backend=backend,
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
        # PR-M intentionally does NOT wrap the SSE path in @traceable
        # — wrapping would force buffering (langsmith records the full
        # output) and break the progressive token UX the Frontend chat
        # panel renders. The system-prompt verification surface PR-M
        # adds for the live demo lives on `/v1/complete`; scenarios
        # call compose without `?stream=true`.
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
        embedding: EmbeddingBackend = Depends(get_embedding_backend),
        settings: Settings = Depends(get_settings),
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
        # Per-user personal-skill memory loaded ONCE per request — every
        # tool call within this request shares the same in-memory pool
        # (sub-ms cosine, no file reload, no DB query). Path-1 design
        # (memory `project_personalization_memory_pattern.md`): the
        # JSON file at `{personal_memory_dir}/{user_id}.json` is the
        # canonical store. With `personal_memory_dir=""`,
        # `payload.user_id=None`, or a missing file, the loader returns
        # an empty pool — `run_policy_extract_agent` then declines to
        # register `search_personal_skills`, preserving the cold-start
        # baseline that the GitLab smoke locks in.
        memory_pool = PersonalMemoryPool.load(
            settings.personal_memory_dir or None,
            payload.user_id,
        )

        # Industry-baseline pool is loaded the same way — once per
        # request, scoped to the chunk's domain, embeddings cached at
        # the module level so subsequent same-domain requests in the
        # same container skip the BGE-M3 pass. With `domain="other"`
        # (smoke default), an unseeded domain, or no embedding backend,
        # `load()` returns an empty pool and the agent declines to
        # register `search_industry_baselines` — same regression
        # guarantee PR-γ established for personal memory (memory
        # `project_personalization_memory_pattern.md`).
        baseline_pool = await IndustryBaselinePool.load(
            settings.industry_baseline_dir or None,
            payload.domain,
            embedding,
        )

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
                    memory_pool=memory_pool,
                    embedding_backend=embedding,
                    baseline_pool=baseline_pool,
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

    @app.post(
        "/v1/personalization/extract_from_diff",
        response_model=PersonalizationExtractResponse,
    )
    async def personalization_extract_from_diff(
        payload: PersonalizationExtractRequest,
        backend: LLMBackend = Depends(get_backend),
    ) -> PersonalizationExtractResponse:
        """Compute a workflow diff and run propose+judge to surface a
        personal_skill candidate from one HITL edit (PLAN_14 §3).

        AI_Agent is stateless on this endpoint — no DB write, no
        per-user state. API_Server (PR-G) consumes the response and
        persists the candidate / review rows. The `rejected_hashes`
        the caller passes is the only user-scoping signal that reaches
        the agent, and the route plumbs `user_id` through to the
        structured log line so operators can attribute accepts.

        `langsmith_run_id` is minted here (same pattern as
        `/v1/policy/extract_reflective`) and stamped onto the response
        only when LangSmith tracing is active — clients paste the UUID
        into the LangSmith UI to find the run.
        """
        tracing_on = (
            os.environ.get("LANGCHAIN_TRACING_V2", "").strip().lower()
            in {"1", "true", "yes", "on"}
            and bool(os.environ.get("LANGCHAIN_API_KEY", "").strip())
        )
        run_id = str(uuid.uuid4()) if tracing_on else None

        response = await extract_personalization_from_diff(
            backend,
            v1=payload.v1,
            v2=payload.v2,
            rejected_hashes=payload.rejected_hashes,
            user_id=payload.user_id,
        )
        if run_id is not None:
            response = response.model_copy(update={"langsmith_run_id": run_id})
        return response

    @app.post(
        "/v1/personalization/memory/upsert",
        response_model=PersonalMemoryUpsertResponse,
    )
    async def personalization_memory_upsert(
        payload: PersonalMemoryUpsertRequest,
        embedding: EmbeddingBackend = Depends(get_embedding_backend),
        settings: Settings = Depends(get_settings),
    ) -> PersonalMemoryUpsertResponse:
        """Persist one personal-skill row into the user's memory file
        (PR-I write side, closing the PLAN_14 retrieval loop).

        API_Server's `activate_candidate` hits this after the DB
        transition so the next `/v1/policy/extract_reflective` request
        for the same user finds the row in the in-memory pool. The
        endpoint is server-side stateless beyond the file write — every
        per-user signal arrives in the body.

        503 when `personal_memory_dir` is unset (feature disabled in
        this deployment); 422 when the user_id contains characters that
        could escape the base directory; 500 when the file write fails.
        """
        if not settings.personal_memory_dir:
            raise HTTPException(
                status_code=503,
                detail="personal_memory_dir is not configured",
            )

        skill = payload.skill
        if skill.embedding is not None and len(skill.embedding) > 0:
            vector = [float(x) for x in skill.embedding]
            embedding_source = "caller"
        else:
            # The condition.text + action.text mirror the surface the
            # reflective agent's `search_personal_skills` tool will
            # match against — using the same string here keeps the
            # cosine geometry consistent across read and write.
            condition_text = (
                (skill.condition.get("text") if isinstance(skill.condition, dict) else None)
                or ""
            )
            action_text = (
                (skill.action.get("text") if isinstance(skill.action, dict) else None)
                or ""
            )
            text = (condition_text + " " + action_text).strip() or skill.id
            vectors = await embedding.embed([text])
            if not vectors or not vectors[0]:
                raise HTTPException(
                    status_code=502,
                    detail="embedding backend returned empty vector",
                )
            vector = [float(x) for x in vectors[0]]
            embedding_source = "server"

        entry = PersonalSkillEntry(
            id=skill.id,
            condition=skill.condition,
            action=skill.action,
            suggestion_hash=skill.suggestion_hash,
            embedding=vector,
            source=skill.source,
            first_observed_at=skill.first_observed_at,
            active=skill.active,
        )

        try:
            pool_size = await upsert_personal_skill(
                base_dir=settings.personal_memory_dir,
                user_id=payload.user_id,
                entry=entry,
            )
        except PersonalMemoryWriteError as exc:
            # `unsafe user_id` and `dir not configured` collapse into
            # 422 — both are caller-side bugs the API_Server proxy can
            # surface back to the operator without retry.
            msg = str(exc).lower()
            if "unsafe" in msg or "not configured" in msg:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return PersonalMemoryUpsertResponse(
            ok=True,
            pool_size=pool_size,
            embedding_source=embedding_source,
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
