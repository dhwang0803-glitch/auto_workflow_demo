"""Tool primitives for the prompt-engineered agent loop (ADR-024).

A `Tool` bundles the four things the agent loop needs:
- `name` — identifier the LLM emits in `<tool_call name="...">` blocks.
- `description` — one-sentence purpose, rendered into the system prompt
  so the LLM knows when to reach for the tool.
- `parameters` — minimal JSON-schema-ish dict (`{"arg": "type — desc"}`)
  used only for prompt rendering. We do NOT validate the LLM's args
  against this schema; handlers validate what they need. The prompt
  spec is descriptive, not enforcing.
- `handler` — async callable that takes the parsed args dict and
  returns any JSON-serializable value (the agent loop will
  json.dumps(default=str) the return into the next observation).

The schema is NOT enforced at the loop level on purpose. Gemma 4 via
prompt-engineered tool calling occasionally emits arg keys that drift
from the schema (extra fields, type coercions). Strict validation would
turn every drift into a hard parse_error and the loop would lose work
the handler could have shrugged off. Handlers do their own contract
checking — same posture as `app.services._llm_json` parsing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping


ToolHandler = Callable[[Mapping[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: Mapping[str, str]
    handler: ToolHandler


def render_tool_catalog(tools: list[Tool]) -> str:
    """Format tools for inclusion in the agent's system prompt.

    Output is plain markdown — Gemma 4 reads markdown headings well in
    Phase 2 sweep results (memory `reference_gemma4_reasoning_trace.md`)
    and the `### NAME` heading also doubles as a stable section anchor
    if a future review wants to grep for a specific tool's prompt copy.
    """
    if not tools:
        return "(no tools available)"
    parts: list[str] = []
    for t in tools:
        parts.append(f"### {t.name}")
        parts.append(t.description.strip())
        if t.parameters:
            parts.append("")
            parts.append("Parameters:")
            for arg, desc in t.parameters.items():
                parts.append(f"- `{arg}` — {desc}")
        parts.append("")  # blank line between tools
    return "\n".join(parts).rstrip()
