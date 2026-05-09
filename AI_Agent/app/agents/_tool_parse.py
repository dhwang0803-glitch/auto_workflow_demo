"""Parse `<tool_call>` and `<finish>` blocks from agent LLM output.

The agent loop expects every assistant turn to end with exactly ONE
of these blocks (ADR-024 §3):

    <tool_call name="TOOL_NAME">
    {JSON arguments}
    </tool_call>

    <finish>
    {JSON result}
    </finish>

Anything before the block is treated as the model's "thinking" / pre-
amble — kept for the trace but not parsed. `<finish>` takes precedence
over `<tool_call>` when both appear (the spec disallows both, but if a
model emits both we honor the terminal one to avoid loops).

Why XML-style tags rather than JSON-only function calls:
- Gemma 4 via llama.cpp does not have stable native tool calling. We
  use prompt-engineered output, same posture as `judge.py`.
- XML tags survive Markdown fenced code blocks, brace-mismatched JSON
  drafts in the surrounding prose, and reasoning that mentions other
  JSON. The tag pair is a coarse but reliable bracket.
- The format mirrors Anthropic's `<tool_use>` content blocks closely
  enough that a future swap to native tool calling is mechanical.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


# DOTALL so multi-line JSON bodies match. Tool names are conservative —
# `[A-Za-z_][A-Za-z0-9_]*` matches Python identifier shape, which is
# also what we use for tool names downstream (extract_policies, etc.).
_TOOL_CALL_RE = re.compile(
    r'<tool_call\s+name="([A-Za-z_][A-Za-z0-9_]*)">(.*?)</tool_call>',
    re.DOTALL,
)
_FINISH_RE = re.compile(r"<finish>(.*?)</finish>", re.DOTALL)


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class Finish:
    result: Any


class ToolParseError(ValueError):
    """The assistant turn did not contain a parseable terminal block.

    Carries `raw` so the agent loop can record it on the step trace —
    debugging a stuck agent is impossible without seeing what the model
    actually emitted.
    """

    def __init__(self, message: str, raw: str = "") -> None:
        super().__init__(message)
        self.raw = raw


def parse_action(text: str) -> ToolCall | Finish:
    """Return the terminal action from one assistant turn.

    Precedence: `<finish>` wins over `<tool_call>` if both appear, since
    the model has signalled it is done.

    Raises `ToolParseError` if no recognized block is present or if the
    block's body is not valid JSON.
    """
    finish_match = _FINISH_RE.search(text)
    if finish_match:
        body = finish_match.group(1).strip()
        body = _strip_code_fence(body)
        if not body:
            # Empty <finish></finish> — treat as "done with no payload".
            # Some agent loops want to signal termination without a
            # structured result; returning None preserves that option.
            return Finish(result=None)
        try:
            return Finish(result=json.loads(body))
        except json.JSONDecodeError as exc:
            raise ToolParseError(
                f"<finish> body is not valid JSON: {exc}", raw=text
            ) from exc

    call_match = _TOOL_CALL_RE.search(text)
    if call_match:
        name = call_match.group(1)
        body = call_match.group(2).strip()
        body = _strip_code_fence(body)
        if not body:
            # Empty body → no-arg call. Common shape for tools like
            # `finalize()` that take nothing.
            return ToolCall(name=name, args={})
        try:
            args = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ToolParseError(
                f"<tool_call name={name!r}> body is not valid JSON: {exc}",
                raw=text,
            ) from exc
        if not isinstance(args, dict):
            raise ToolParseError(
                f"<tool_call name={name!r}> body must be a JSON object, "
                f"got {type(args).__name__}",
                raw=text,
            )
        return ToolCall(name=name, args=args)

    raise ToolParseError(
        "no <tool_call> or <finish> block found in response", raw=text
    )


def _strip_code_fence(body: str) -> str:
    """Remove a leading/trailing ```...``` fence if the model wrapped JSON.

    Gemma 4 sometimes emits ```json\\n{...}\\n``` inside the tag body
    when it has been Markdown-conditioned. Strip the fence rather than
    fail the parse — the JSON inside is still well-formed.
    """
    if body.startswith("```") and body.endswith("```"):
        # Strip first line (```json or just ```) and trailing fence
        first_nl = body.find("\n")
        if first_nl == -1:
            return ""
        return body[first_nl + 1 : -3].strip()
    return body
