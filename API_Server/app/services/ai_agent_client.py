"""AIAgentHTTPBackend — LLMBackend implementation that proxies to AI_Agent.

PLAN_11 PR 1 wires this as the preferred backend when
`settings.ai_agent_base_url` is set. Falls back to the in-tree
AnthropicBackend/StubLLMBackend when unset so envs without AI_Agent still
boot.

The HTTP contract lives in AI_Agent/app/models/http.py:
- POST /v1/complete  → `{text}` JSON
- POST /v1/stream    → chunked text/plain

PLAN_12 W2-7 added the skill-bootstrap helpers (classify_domain,
analyze_gaps, answer_to_skill) — these wrap the AI_Agent endpoints
under `/v1/domain/*` and `/v1/skills/*`. They live on the same client
because they share base_url + bearer token, but they are NOT part of
the LLMBackend protocol (which stays minimal: complete + stream).

See AI_Agent/docs/SPLIT.md for the boundary spec.
"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator

import httpx

from app.models.skills import (
    DomainClassificationResponse,
    ExtractedSkillBody,
    PolicyGapBody,
    SkillDraftBody,
)

logger = logging.getLogger(__name__)


class AIAgentHTTPBackend:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_s: float = 180.0,
        bearer_token: str = "",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        # 180s default covers Modal cold-start (boot + 16.9 GiB GGUF mmap +
        # first-turn inference) at the PLAN_12 multi-turn budget. Warm turns
        # finish in ~3-19s (PR #128 risk 1-C) — 180s is a ceiling, not a
        # target. connect timeout stays short so DNS/TCP misbehavior fails
        # fast. See config.py `ai_agent_timeout_s` for the rationale.
        self._timeout = httpx.Timeout(timeout_s, connect=10.0)
        # Header is attached on every request when set; AI_Agent FastAPI
        # middleware (env AGENT_BEARER_TOKEN) checks it. Empty token means
        # no header — only valid for local dev where AI_Agent runs unauthed.
        self._headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}

    async def complete(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
    ) -> str:
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers) as client:
            resp = await client.post(
                f"{self._base_url}/v1/complete",
                json={
                    "system": system,
                    "user_message": user_message,
                    "max_tokens": max_tokens,
                },
            )
            resp.raise_for_status()
            return resp.json()["text"]

    async def stream(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers) as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/v1/stream",
                json={
                    "system": system,
                    "user_message": user_message,
                    "max_tokens": max_tokens,
                },
            ) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_text():
                    if chunk:
                        yield chunk

    # --- PLAN_12 W2-7 skill-bootstrap helpers ---------------------------

    async def _post_json(self, path: str, body: dict) -> Any:
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers) as client:
            resp = await client.post(f"{self._base_url}{path}", json=body)
            resp.raise_for_status()
            return resp.json()

    async def classify_domain(self, text: str) -> DomainClassificationResponse:
        body = await self._post_json("/v1/domain/classify", {"text": text})
        return DomainClassificationResponse.model_validate(body)

    async def analyze_gaps(
        self,
        domain: str,
        extracted_skills: list[ExtractedSkillBody],
    ) -> list[PolicyGapBody]:
        body = await self._post_json(
            "/v1/skills/gap_analyze",
            {
                "domain": domain,
                "extracted_skills": [s.model_dump() for s in extracted_skills],
            },
        )
        # AI_Agent's GapAnalysis = {"missing": [PolicyGap]}. We unwrap
        # because API_Server's BootstrapResponse adds session_id + domain
        # alongside the missing list.
        return [PolicyGapBody.model_validate(p) for p in body["missing"]]

    async def answer_to_skill(
        self,
        *,
        domain: str,
        policy_id: str,
        question: str,
        answer: str,
    ) -> SkillDraftBody:
        body = await self._post_json(
            "/v1/skills/answer_to_skill",
            {
                "domain": domain,
                "policy_id": policy_id,
                "question": question,
                "answer": answer,
            },
        )
        return SkillDraftBody.model_validate(body)
