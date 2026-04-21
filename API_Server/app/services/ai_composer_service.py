"""AI Composer service — Claude-backed DAG generator (PLAN_02 §5).

Architecture:

- `LLMBackend` is a small Protocol with two methods: `complete()` for the
  non-stream JSON-once path and `stream()` for the SSE path. The default
  `AnthropicBackend` wraps the official SDK; tests inject a fake; local
  UI testing can enable `StubLLMBackend` via `AI_COMPOSER_USE_STUB=true`
  to drive the ChatPanel without any Anthropic credentials. This keeps
  Anthropic SDK imports out of the test path entirely.
- `AIComposerService.compose()` builds the prompt (system rules + node
  catalog + current_dag + user message), calls the backend, parses the
  JSON response into a `ComposeResult`, and bubbles up shape errors as
  `InvalidComposerResponseError` (502).
- `AIComposerService.compose_stream()` (PR B) yields `StreamEvent` instances
  — `RationaleDelta` while the model emits text inside `<rationale>...
  </rationale>`, then a single `Result` once the trailing JSON parses.
- Rate limiting is a per-user in-memory token bucket. A future PR replaces
  it with a Redis counter so the limit holds across Cloud Run instances.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol
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

    def stream(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        """Yield text chunks as the model emits them.

        Implementations MUST close the underlying stream when the consumer
        stops iterating (e.g. on client disconnect). The default
        `AnthropicBackend` does this via `async with` around the SDK's
        `messages.stream()` context manager.
        """
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

    async def stream(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        # `messages.stream()` returns an async context manager that tears the
        # HTTP connection down deterministically on exit — including when the
        # consumer stops iterating early (FastAPI client disconnect raises
        # CancelledError inside `async for`, unwinding through `async with`).
        async with self._client.messages.stream(
            model=self._model,
            max_tokens=max_tokens,
            system=[
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}},
            ],
            messages=[{"role": "user", "content": user_message}],
        ) as s:
            async for text in s.text_stream:
                yield text


# ----------------------------------------------------------------- stub backend


class StubLLMBackend:
    """Deterministic, network-free backend for local UI testing.

    Picks an intent from simple keyword rules in the user_message so you can
    drive the ChatPanel end-to-end without an Anthropic key:
    - message contains "?" or starts with "what" / "who" / "which" → clarify
    - user_message payload already embeds a `<current_dag>` that isn't null
      → refine (modifies the first node's config to prove the wire works)
    - otherwise → draft (2-node http_request → gmail_send skeleton)

    The `complete()` path returns the full fenced JSON. `stream()` chunks it
    so the SSE path exercises the `<rationale>` parser. Not intended for
    production — enable via `AI_COMPOSER_USE_STUB=true`.
    """

    async def complete(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
    ) -> str:
        intent, payload = self._decide(user_message)
        return "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"

    async def stream(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        _, payload = self._decide(user_message)
        rationale = payload.get("rationale", "")
        # Emit the rationale a few chars at a time to show the typing effect.
        yield "<rationale>"
        for i in range(0, len(rationale), 8):
            # Tiny sleep so the frontend actually sees the frames arrive one
            # at a time rather than as a single chunk that the browser
            # concatenates before paint.
            await asyncio.sleep(0.04)
            yield rationale[i : i + 8]
        yield "</rationale>\n```json\n"
        yield json.dumps(payload, ensure_ascii=False)
        yield "\n```"

    # ---- intent selection --------------------------------------------------

    def _decide(self, user_message: str) -> tuple[str, dict]:
        text = user_message.lower()
        has_current_dag = (
            "<current_dag>" in text and "<current_dag>\nnull" not in text
        )
        if has_current_dag:
            return "refine", self._refine_payload()
        if (
            "?" in user_message
            or text.strip().startswith(("what", "who", "which", "where", "how"))
        ):
            return "clarify", {
                "intent": "clarify",
                "clarify_questions": [
                    "Which data source should I use?",
                    "Who are the recipients?",
                    "What format should the output take?",
                ],
                "proposed_dag": None,
                "diff": None,
                "rationale": (
                    "I need a bit more detail before drafting a workflow."
                ),
            }
        return "draft", {
            "intent": "draft",
            "clarify_questions": None,
            "proposed_dag": {
                "nodes": [
                    {
                        "id": "fetch_data",
                        "type": "http_request",
                        "config": {
                            "url": "https://example.com/data",
                            "method": "GET",
                        },
                    },
                    {
                        "id": "notify",
                        "type": "gmail_send",
                        "config": {
                            "to": "team@example.com",
                            "subject": "Report",
                            "body": "See attached.",
                        },
                    },
                ],
                "edges": [{"source": "fetch_data", "target": "notify"}],
            },
            "diff": None,
            "rationale": (
                "This is a stubbed draft from StubLLMBackend — fetch data, "
                "then email it to the team. Replace ANTHROPIC_API_KEY with a "
                "real key (and unset AI_COMPOSER_USE_STUB) to get real "
                "Claude-backed responses."
            ),
        }

    def _refine_payload(self) -> dict:
        return {
            "intent": "refine",
            "clarify_questions": None,
            "proposed_dag": {
                "nodes": [
                    {
                        "id": "fetch_data",
                        "type": "http_request",
                        "config": {
                            "url": "https://example.com/data?refined=1",
                            "method": "GET",
                        },
                    },
                ],
                "edges": [],
            },
            "diff": {
                "added_nodes": [],
                "removed_node_ids": [],
                "modified_nodes": [
                    {
                        "id": "fetch_data",
                        "config": {
                            "url": "https://example.com/data?refined=1",
                        },
                    }
                ],
            },
            "rationale": (
                "Stubbed refinement — updated the fetch URL with a "
                "`refined=1` query param to prove the wire."
            ),
        }


# --------------------------------------------------------------- stream events


@dataclass(frozen=True)
class RationaleDelta:
    """Text fragment emitted inside `<rationale>...</rationale>`.

    The Frontend appends these to the live panel verbatim — no JSON parsing,
    no markdown processing on the transport layer.
    """

    token: str


@dataclass(frozen=True)
class Result:
    """Terminal event once the JSON fence is parsed into a `ComposeResult`."""

    payload: ComposeResult


@dataclass(frozen=True)
class StreamError:
    """Terminal failure event. `code` is machine-readable so the Frontend can
    distinguish "retry later" (rate_limit) from "this will never work"
    (invalid_response)."""

    code: str
    message: str


StreamEvent = RationaleDelta | Result | StreamError


class _RationaleStreamParser:
    """State machine that splits a token stream into:

    1. `RationaleDelta` events while text is inside `<rationale>...</rationale>`
    2. A single buffered string (accessible via `.json_tail`) accumulating
       everything AFTER `</rationale>`, which the caller parses once the
       stream ends.

    We can't rely on the model emitting tag boundaries on chunk boundaries —
    the SDK chunks are arbitrary. The parser keeps a tiny look-behind buffer
    the size of the longest tag so partial tag matches don't leak as user-
    visible tokens.
    """

    _OPEN = "<rationale>"
    _CLOSE = "</rationale>"

    def __init__(self) -> None:
        # Pre-tag chunks are discarded (some models emit a newline before the
        # tag); post-close chunks accumulate verbatim for JSON parsing.
        self._state: str = "PRE"  # PRE → IN → POST
        self._buf: str = ""
        self.json_tail: str = ""

    def feed(self, chunk: str) -> list[RationaleDelta]:
        self._buf += chunk
        out: list[RationaleDelta] = []
        while True:
            if self._state == "PRE":
                idx = self._buf.find(self._OPEN)
                if idx == -1:
                    # Keep a small tail in case the open tag straddles chunks.
                    self._buf = self._buf[-(len(self._OPEN) - 1):]
                    return out
                self._buf = self._buf[idx + len(self._OPEN):]
                self._state = "IN"
            elif self._state == "IN":
                idx = self._buf.find(self._CLOSE)
                if idx == -1:
                    # Emit everything except a tail that might be the start of
                    # `</rationale>`. Without this guard a chunk ending with
                    # "</ration" would leak as user-visible text.
                    keep = len(self._CLOSE) - 1
                    emit = self._buf[:-keep] if len(self._buf) > keep else ""
                    if emit:
                        out.append(RationaleDelta(token=emit))
                        self._buf = self._buf[len(emit):]
                    return out
                if idx > 0:
                    out.append(RationaleDelta(token=self._buf[:idx]))
                self._buf = self._buf[idx + len(self._CLOSE):]
                self._state = "POST"
            else:  # POST
                self.json_tail += self._buf
                self._buf = ""
                return out

    def finish(self) -> list[RationaleDelta]:
        # Flush any remainder that was being held back as potential tag prefix.
        out: list[RationaleDelta] = []
        if self._state == "IN" and self._buf:
            out.append(RationaleDelta(token=self._buf))
            self._buf = ""
        elif self._state == "POST" and self._buf:
            self.json_tail += self._buf
            self._buf = ""
        return out


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
        system = self._build_system_prompt(catalog, for_streaming=False)
        user_payload = self._build_user_message(current_dag, message)

        raw = await self._backend.complete(
            system=system,
            user_message=user_payload,
            max_tokens=self._max_tokens,
        )
        return self._parse_result(raw)

    async def compose_stream(
        self,
        *,
        user_id: UUID,
        message: str,
        current_dag: dict | None,
    ) -> AsyncIterator[StreamEvent]:
        """Yield `RationaleDelta` events as the model narrates, then a single
        `Result` (or `StreamError`) once the trailing JSON parses.

        The enabled/rate-limit/disabled checks are emitted *in-band* as
        `StreamError` events rather than raised, so the router can send a
        clean SSE "error" frame instead of aborting the response mid-stream.
        The router still uses the DomainError path for pre-stream failures
        (auth 401 etc.) that happen before any bytes are sent.
        """
        if self._backend is None:
            yield StreamError(
                code="disabled",
                message="AI Composer is not configured (anthropic_api_key missing)",
            )
            return
        try:
            await self._rate_limiter.acquire(str(user_id))
        except ComposerRateLimitError as exc:
            yield StreamError(code="rate_limit", message=exc.message)
            return

        catalog = await self._catalog_provider()
        system = self._build_system_prompt(catalog, for_streaming=True)
        user_payload = self._build_user_message(current_dag, message)

        parser = _RationaleStreamParser()
        try:
            async for chunk in self._backend.stream(
                system=system,
                user_message=user_payload,
                max_tokens=self._max_tokens,
            ):
                for ev in parser.feed(chunk):
                    yield ev
            for ev in parser.finish():
                yield ev
        except asyncio.CancelledError:
            # Client disconnect — propagate so the underlying HTTP stream
            # unwinds via `async with`. No terminal event needed; the socket
            # is already gone.
            raise
        except Exception as exc:  # noqa: BLE001 — surface SDK/network errors
            logger.exception("composer_stream_upstream_error")
            yield StreamError(code="upstream_error", message=str(exc))
            return

        try:
            result = self._parse_result(parser.json_tail)
        except InvalidComposerResponseError as exc:
            yield StreamError(code="invalid_response", message=exc.message)
            return
        yield Result(payload=result)

    # -------------------------------------------------------- prompt build

    def _build_system_prompt(
        self, catalog: list[dict[str, Any]], *, for_streaming: bool
    ) -> str:
        catalog_json = json.dumps(catalog, ensure_ascii=False)
        # In streaming mode the model MUST narrate first inside
        # `<rationale>...</rationale>` so the UI can show live tokens, then
        # emit the JSON fence. In non-stream mode we can fit the rationale
        # inside the JSON payload — simpler and one less parsing step.
        if for_streaming:
            output_contract = (
                "Output format (streaming mode):\n"
                "  1. First, write the rationale as a user-facing explanation "
                "inside `<rationale>...</rationale>`. Use the user's language. "
                "Do NOT emit any text before `<rationale>`.\n"
                "  2. Then emit a single JSON object inside a ```json fenced "
                "block. Schema:\n"
                "{\n"
                '  "intent": "clarify"|"draft"|"refine",\n'
                '  "clarify_questions": [string, ...] | null,\n'
                '  "proposed_dag": {"nodes": [...], "edges": [...]} | null,\n'
                '  "diff": {"added_nodes": [...], "removed_node_ids": [...], '
                '"modified_nodes": [...]} | null,\n'
                '  "rationale": string  // repeat of the rationale above\n'
                "}\n"
            )
        else:
            output_contract = (
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
            )
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
            f"{output_contract}"
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
