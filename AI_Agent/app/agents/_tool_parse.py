"""Parse a JSON action envelope from agent LLM output.

The agent loop expects every assistant turn to end with exactly ONE
JSON object (ADR-024 §3, revised 2026-05-09):

    {"thought": "...", "action": "tool_call", "name": "TOOL_NAME", "args": {...}}
    {"thought": "...", "action": "finish",    "result": ...JSON answer...}

`thought` is optional. Reasoning prose may precede the JSON block; the
parser walks the response and takes the LAST top-level JSON object.

Why JSON rather than XML tags:
- Gemma 4 (RLHF'd toward JSON-formatted reasoning) reliably falls back
  to `{"thought": "..."}` shape regardless of how strongly we prescribe
  `<tool_call>` / `<finish>` tags. Embracing JSON aligns the wire with
  the model's training distribution, making the format a help rather
  than a fight (2026-05-09 PR-β regression — XML envelope produced 5/5
  parse_error on Gemma 4 26B Q4).
- A single object collapses "reasoning + decision" into one structure
  the model already produces naturally.
- `json.JSONDecoder.raw_decode` lets us walk the response and pick the
  last object, so reasoning prose that mentions JSON examples does not
  trip the parser.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class Finish:
    result: Any


class ToolParseError(ValueError):
    """The assistant turn did not contain a parseable action envelope.

    Carries `raw` so the agent loop can record it on the step trace —
    debugging a stuck agent is impossible without seeing what the model
    actually emitted.
    """

    def __init__(self, message: str, raw: str = "") -> None:
        super().__init__(message)
        self.raw = raw


def parse_action(text: str) -> ToolCall | Finish:
    """Return the terminal action from one assistant turn.

    Walks `text` for top-level JSON objects via `json.JSONDecoder.raw_decode`
    and uses the LAST one (so the model can sketch examples in prose
    without tripping the parser). The `action` discriminator selects
    between `tool_call` and `finish`.

    Raises `ToolParseError` if no JSON object is found, the envelope is
    malformed, or `action` is missing/unknown.
    """
    obj = _extract_last_json_object(text)
    if obj is None:
        raise ToolParseError(
            "no JSON action object found in response", raw=text
        )
    if not isinstance(obj, dict):
        raise ToolParseError(
            f"action envelope must be a JSON object, got {type(obj).__name__}",
            raw=text,
        )

    action = obj.get("action")
    if action == "tool_call":
        name = obj.get("name")
        if not isinstance(name, str) or not name:
            raise ToolParseError(
                "tool_call envelope missing 'name' (string)", raw=text
            )
        args = obj.get("args", {})
        if not isinstance(args, dict):
            raise ToolParseError(
                f"tool_call 'args' must be a JSON object, "
                f"got {type(args).__name__}",
                raw=text,
            )
        return ToolCall(name=name, args=args)

    if action == "finish":
        # `result` absent → caller signaled completion with no payload.
        return Finish(result=obj.get("result"))

    raise ToolParseError(
        f"unknown action {action!r} (expected 'tool_call' or 'finish')",
        raw=text,
    )


def _extract_last_json_object(text: str) -> Any:
    """Return the rightmost top-level JSON value parseable in `text`.

    Uses `json.JSONDecoder.raw_decode` to walk the string, recording each
    successful parse. The action envelope is expected to be the last
    such object — reasoning prose, even if it mentions example JSON,
    is overridden by the final block.
    """
    decoder = json.JSONDecoder()
    last: Any = None
    i = 0
    n = len(text)
    while i < n:
        j = text.find("{", i)
        if j == -1:
            break
        try:
            obj, end = decoder.raw_decode(text, idx=j)
            last = obj
            i = end
        except json.JSONDecodeError:
            i = j + 1
    return last
