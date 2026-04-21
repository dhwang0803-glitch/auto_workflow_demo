"""AI Composer service — Claude-backed DAG generator (PLAN_02 §5).

Architecture:

- `LLMBackend` is a small Protocol with a single `complete()` method. The
  default `AnthropicBackend` wraps the official SDK; tests inject a fake.
  This keeps Anthropic SDK imports out of the test path entirely.
- `AIComposerService.compose()` builds the prompt (system rules + node
  catalog + current_dag + user message), calls the backend, parses the
  JSON response into a `ComposeResult`, and bubbles up shape errors as
  `InvalidComposerResponseError` (502).
- Rate limiting is a per-user in-memory token bucket. PR B replaces it
  with a Redis counter so the limit holds across Cloud Run instances.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import deque
from typing import Any, Awaitable, Callable, Protocol
from uuid import UUID

from pydantic import ValidationError

from app.errors import DomainError
from app.models.ai_composer import ComposeResult


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- exceptions


class ComposerDisabledError(DomainError):
    """503 — `anthropic_api_key` not configured. Surfaces a clear message
    instead of an opaque 500 from the SDK."""

    http_status = 503


class InvalidComposerResponseError(DomainError):
    """502 — LLM returned malformed JSON or a payload that doesn't match
    `ComposeResult`. The user can retry; the operator should inspect logs."""

    http_status = 502


class ComposerRateLimitError(DomainError):
    """429 — per-user request budget exhausted. PR A in-memory only."""

    http_status = 429
    headers = {"Retry-After": "60"}


# ----------------------------------------------------------------- backend


class LLMBackend(Protocol):
    async def complete(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
    ) -> str:
        """Return the assistant's reply as raw text."""
        ...


class AnthropicBackend:
    """Default backend — wraps the official `anthropic` SDK.

    Imported lazily so test envs without the SDK installed can still import
    this module. Production containers always have the dep.
    """

    def __init__(self, *, api_key: str, model: str) -> None:
        from anthropic import AsyncAnthropic  # local import — see docstring

        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    async def complete(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
    ) -> str:
        msg = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=[
                # Cache the system prompt — the node catalog dominates token
                # count and is identical across a session. Anthropic returns
                # cache_read_input_tokens > 0 on the second call onward.
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}},
            ],
            messages=[{"role": "user", "content": user_message}],
        )
        # `.content` is a list of blocks; collect any text.
        parts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
        return "".join(parts)


# --------------------------------------------------------------- rate limit


class _InMemoryRateLimiter:
    """Per-user sliding-window counter. `acquire()` raises if the user has
    already used the budget within the last 60s.

    PR B replaces this with a Redis INCR + EXPIRE so the limit applies
    cluster-wide. The interface stays the same.
    """

    def __init__(self, *, per_minute: int) -> None:
        self._per_minute = per_minute
        self._buckets: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, user_id: str) -> None:
        async with self._lock:
            now = time.monotonic()
            bucket = self._buckets.setdefault(user_id, deque())
            # Drop timestamps older than the window.
            while bucket and now - bucket[0] > 60.0:
                bucket.popleft()
            if len(bucket) >= self._per_minute:
                raise ComposerRateLimitError(
                    f"AI Composer rate limit ({self._per_minute}/min) exceeded; "
                    "retry in a minute"
                )
            bucket.append(now)


# ----------------------------------------------------------------- service


# The model is instructed to wrap structured output in this fence so we can
# extract it deterministically even if it adds prose around it. Failing to
# find the fence falls back to "the entire reply must be JSON".
_JSON_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


CatalogProvider = Callable[[], Awaitable[list[dict[str, Any]]]]


class AIComposerService:
    def __init__(
        self,
        *,
        backend: LLMBackend | None,
        catalog_provider: CatalogProvider,
        rate_per_minute: int,
        max_tokens: int,
    ) -> None:
        # `backend=None` means the service is wired but disabled (no API key).
        # We let it instantiate so the container/router don't have to special-case;
        # `compose()` raises ComposerDisabledError on call.
        self._backend = backend
        self._catalog_provider = catalog_provider
        self._max_tokens = max_tokens
        self._rate_limiter = _InMemoryRateLimiter(per_minute=rate_per_minute)

    async def compose(
        self,
        *,
        user_id: UUID,
        message: str,
        current_dag: dict | None,
    ) -> ComposeResult:
        if self._backend is None:
            raise ComposerDisabledError(
                "AI Composer is not configured (anthropic_api_key missing)"
            )
        await self._rate_limiter.acquire(str(user_id))

        catalog = await self._catalog_provider()
        system = self._build_system_prompt(catalog)
        user_payload = self._build_user_message(current_dag, message)

        raw = await self._backend.complete(
            system=system,
            user_message=user_payload,
            max_tokens=self._max_tokens,
        )
        return self._parse_result(raw)

    # -------------------------------------------------------- prompt build

    def _build_system_prompt(self, catalog: list[dict[str, Any]]) -> str:
        catalog_json = json.dumps(catalog, ensure_ascii=False)
        return (
            "You are a workflow-automation agent. The user describes an "
            "intent in natural language and you produce a directed acyclic "
            "graph (DAG) of nodes selected ONLY from the provided catalog.\n"
            "\n"
            "Decide one of three intents per turn:\n"
            "  - 'clarify' when the request is ambiguous (data source, "
            "recipients, template). Return up to 3 questions in "
            "clarify_questions and leave proposed_dag null.\n"
            "  - 'draft' when there is no current_dag and the request is "
            "specific enough. Return proposed_dag (nodes + edges) and "
            "leave diff null.\n"
            "  - 'refine' when current_dag is non-null. Return the full "
            "proposed_dag PLUS a diff describing what changed.\n"
            "\n"
            "Output a single JSON object inside a ```json fenced block. "
            "Schema:\n"
            "{\n"
            '  "intent": "clarify"|"draft"|"refine",\n'
            '  "clarify_questions": [string, ...] | null,\n'
            '  "proposed_dag": {"nodes": [...], "edges": [...]} | null,\n'
            '  "diff": {"added_nodes": [...], "removed_node_ids": [...], '
            '"modified_nodes": [...]} | null,\n'
            '  "rationale": string  // why this DAG, in the user language\n'
            "}\n"
            "\n"
            "Each node has: id (string), type (one of catalog types), "
            "config (object matching that node's config_schema). Each edge "
            "has source and target node ids. Never invent node types not "
            "in the catalog.\n"
            "\n"
            "<node_catalog>\n"
            f"{catalog_json}\n"
            "</node_catalog>\n"
        )

    def _build_user_message(self, current_dag: dict | None, message: str) -> str:
        current_json = json.dumps(current_dag) if current_dag else "null"
        return (
            f"<current_dag>\n{current_json}\n</current_dag>\n"
            f"<user_message>\n{message}\n</user_message>"
        )

    # ----------------------------------------------------------- parsing

    def _parse_result(self, raw: str) -> ComposeResult:
        match = _JSON_FENCE_RE.search(raw)
        candidate = match.group(1) if match else raw.strip()
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError as exc:
            logger.warning("composer_invalid_json", extra={"raw": raw[:500]})
            raise InvalidComposerResponseError(
                "AI Composer reply was not valid JSON"
            ) from exc
        try:
            return ComposeResult.model_validate(data)
        except ValidationError as exc:
            logger.warning(
                "composer_schema_mismatch",
                extra={"errors": exc.errors(), "raw": raw[:500]},
            )
            raise InvalidComposerResponseError(
                "AI Composer reply did not match the expected schema"
            ) from exc


def build_node_catalog_provider() -> CatalogProvider:
    """Default catalog provider — re-imports the Execution_Engine registry on
    every call. Cheap (registry is in-memory). PR B can wrap this with a
    lru_cache if profiling shows hotspot."""

    async def _provider() -> list[dict[str, Any]]:
        # Function-local imports — same pattern as routers/node_catalog.py.
        # API_Server is importable in environments where Execution_Engine
        # isn't installed.
        import src.nodes  # noqa: F401 — triggers self-registration
        from src.nodes.registry import registry

        out: list[dict[str, Any]] = []
        for node_type in registry.list_types():
            cls = registry.get(node_type)
            out.append(
                {
                    "type": node_type,
                    "display_name": cls.display_name or node_type,
                    "category": cls.category,
                    "description": cls.description,
                    "config_schema": cls.config_schema,
                }
            )
        return out

    return _provider
