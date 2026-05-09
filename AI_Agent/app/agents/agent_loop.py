"""ReAct-style agent loop for prompt-engineered tool use (ADR-024 §3).

The loop drives an `LLMBackend` through repeated turns where each turn
must end in exactly one `<tool_call>` or `<finish>` block. The loop
parses the action, dispatches the tool (or terminates), and feeds the
observation back as the next user message.

Termination reasons (mirrors PLAN_13's reflective workflow vocabulary
so trace-tree readers don't have to context-switch):

  - `finish` — model emitted `<finish>`. `result.final` is set.
  - `parse_error` — model output was unparseable. `result.final = None`.
  - `tool_not_found` — model called a tool that isn't in the registry.
    Surfaces as a user-message observation `error: tool 'X' not registered`
    on first occurrence; if the model does it again immediately we treat
    it as no_progress to break loops.
  - `max_iter_exhausted` — budget hit before `<finish>`.
  - `no_progress` — same (tool, args) twice in a row, suggests the
    model is stuck. Cheaper brake than waiting for max_iter.

The loop is intentionally policy-free about what tools mean. PLAN_15
agents (extract / search_personal / search_baseline / etc.) wire the
specific tool registry in PR-β; this module knows nothing about
policies.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

from app.agents._tool_parse import (
    Finish,
    ToolCall,
    ToolParseError,
    parse_action,
)
from app.agents.tool import Tool, render_tool_catalog
from app.agents.tracing import traceable
from app.backends.protocols import LLMBackend

logger = logging.getLogger(__name__)


TerminationReason = Literal[
    "finish",
    "parse_error",
    "tool_not_found",
    "max_iter_exhausted",
    "no_progress",
]


@dataclass(frozen=True)
class AgentStep:
    """One turn of (assistant response → action → observation).

    `raw` is the full model output, including any preamble before the
    terminal block — useful when debugging why the model picked a tool.

    Exactly one of `tool_call` / `finish` / `error` is non-None, and
    `observation` is set when a tool was dispatched (None for finish or
    parse errors).
    """

    raw: str
    tool_call: tuple[str, dict[str, Any]] | None = None
    finish: Any = None
    error: str | None = None
    observation: str | None = None


@dataclass
class AgentResult:
    final: Any
    terminated_reason: TerminationReason
    steps: list[AgentStep] = field(default_factory=list)


# Output-format spec the system prompt always carries. Kept as a single
# constant so PLAN_15 agents don't accidentally reword it — drift in
# this section moves the model's ability to emit valid envelopes.
_OUTPUT_FORMAT = """\
## Output format

End every response with EXACTLY ONE JSON action object. A `thought`
field is optional but encouraged for sketching your reasoning.

To use a tool:

{"thought": "why I am calling this tool", "action": "tool_call", "name": "TOOL_NAME", "args": {"arg": "value"}}

When you are done:

{"thought": "why I am finishing", "action": "finish", "result": ...your final answer as JSON...}

Rules:
- The action object must be the LAST JSON object in your reply.
- `action` MUST be exactly "tool_call" or "finish" — no other values.
- For `tool_call`: `args` MUST be a JSON object (use {} for no-arg tools).
- If a tool returns an error, decide whether to retry with different
  args, switch tools, or finish with what you have.
"""


@traceable(name="agent_loop", run_type="chain")
async def run_agent(
    backend: LLMBackend,
    *,
    system_goal: str,
    user_request: str,
    tools: Sequence[Tool],
    max_iter: int = 8,
    max_tokens_per_turn: int = 1024,
) -> AgentResult:
    """Drive `backend` through tool-using turns until finish or budget exhausted.

    Args:
        backend: Any `LLMBackend`. The loop only uses `complete()`.
        system_goal: Task-specific framing prepended to the system prompt.
        user_request: First user-turn payload (the actual task input).
        tools: Tools the model may call. Order is preserved in the
            catalog rendering, which can subtly bias selection — keep
            the most-likely first call first.
        max_iter: Hard upper bound on assistant turns. Default 8 covers
            the policy_extract case (search × 2 + extract × 2 + eval × 2
            + finish + slack) without being permissive enough to hide
            runaway loops.
        max_tokens_per_turn: Per-call output cap. 1024 is the multi-turn
            budget set in `Settings.ai_compose_max_tokens` (ADR-022 §6).

    Returns:
        `AgentResult` with the final value (or None) and the full step
        trace. The trace is the input to LangSmith / debugging UI.
    """
    tools_by_name = {t.name: t for t in tools}
    system_prompt = (
        f"{system_goal.strip()}\n\n"
        f"## Available tools\n\n{render_tool_catalog(list(tools))}\n\n"
        f"{_OUTPUT_FORMAT}"
    )

    # `transcript` accumulates the conversation as plain text — Gemma 4
    # via the existing `LLMBackend.complete(system, user_message)` API
    # only takes one user_message per call, so we serialize the running
    # history into that single field. Roles are tagged so the model can
    # tell tool results apart from prior reasoning.
    transcript: list[str] = [f"## User request\n\n{user_request}"]

    steps: list[AgentStep] = []
    last_action_signature: tuple[str, str] | None = None

    for _ in range(max_iter):
        user_message = "\n\n".join(transcript)
        raw = await backend.complete(
            system=system_prompt,
            user_message=user_message,
            max_tokens=max_tokens_per_turn,
        )

        try:
            action = parse_action(raw)
        except ToolParseError as exc:
            steps.append(AgentStep(raw=raw, error=str(exc)))
            return AgentResult(
                final=None, terminated_reason="parse_error", steps=steps
            )

        if isinstance(action, Finish):
            steps.append(AgentStep(raw=raw, finish=action.result))
            return AgentResult(
                final=action.result, terminated_reason="finish", steps=steps
            )

        # Tool call dispatch
        assert isinstance(action, ToolCall)
        # No-progress check — same tool + same args twice in a row.
        # `default=str` on dumps so Pydantic models / dataclasses in args
        # don't crash signature comparison.
        signature = (action.name, json.dumps(action.args, sort_keys=True, default=str))
        if signature == last_action_signature:
            steps.append(
                AgentStep(
                    raw=raw,
                    tool_call=(action.name, dict(action.args)),
                    error="no_progress: identical call repeated",
                )
            )
            return AgentResult(
                final=None, terminated_reason="no_progress", steps=steps
            )
        last_action_signature = signature

        tool = tools_by_name.get(action.name)
        if tool is None:
            obs = f"error: tool {action.name!r} is not registered"
            steps.append(
                AgentStep(
                    raw=raw,
                    tool_call=(action.name, dict(action.args)),
                    error=obs,
                    observation=obs,
                )
            )
            transcript.append(_render_assistant_turn(raw))
            transcript.append(_render_observation(action.name, obs))
            # Continue the loop — the model gets the error obs and can
            # pick a different tool. If it makes the same bad call again
            # the no_progress brake catches it next iter.
            continue

        try:
            handler_result = await tool.handler(dict(action.args))
            obs = _render_result(handler_result)
        except Exception as exc:  # noqa: BLE001 — tools are user code
            logger.warning(
                "tool %s raised %s: %s — forwarding to model as obs",
                action.name,
                type(exc).__name__,
                exc,
            )
            obs = f"error: {type(exc).__name__}: {exc}"

        steps.append(
            AgentStep(
                raw=raw,
                tool_call=(action.name, dict(action.args)),
                observation=obs,
            )
        )
        transcript.append(_render_assistant_turn(raw))
        transcript.append(_render_observation(action.name, obs))

    return AgentResult(
        final=None, terminated_reason="max_iter_exhausted", steps=steps
    )


def _render_assistant_turn(raw: str) -> str:
    return f"## Assistant\n\n{raw.strip()}"


def _render_observation(tool_name: str, observation: str) -> str:
    return (
        f"## Tool result\n\n"
        f'<tool_result tool="{tool_name}">\n{observation}\n</tool_result>'
    )


def _render_result(value: Any) -> str:
    """JSON-serialize a tool's return for the next user turn.

    `default=str` lets Pydantic models / pathlib.Path / datetime fall
    through to their string repr rather than crashing the loop. The
    model only reads the resulting text — exact JSON fidelity matters
    less than non-fragility.
    """
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as exc:
        return f"error: tool returned non-serializable value: {exc}"
