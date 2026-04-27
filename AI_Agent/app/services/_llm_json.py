"""Shared JSON-extraction helper for LLM responses.

Different LLM backends garnish their JSON differently — Anthropic loves
```json fences, smaller open-source models add a polite preamble before the
object, llama.cpp grammar-constrained outputs are usually clean. This
helper papers over those variants so each service's parser can assume "the
first balanced {...} block in the response is mine".
"""
from __future__ import annotations

import json
import re

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class JsonExtractError(ValueError):
    """The model response did not contain a parseable JSON object."""


def extract_json_object(raw: str) -> dict:
    """Pull the first balanced JSON object out of a model response.

    Tolerates ```json fences and stray prose before/after the object.
    Raises JsonExtractError on no-object / unbalanced / malformed.
    """
    text = raw.strip()
    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()

    start = text.find("{")
    if start == -1:
        raise JsonExtractError(f"no JSON object in response: {raw!r}")

    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError as exc:
                    raise JsonExtractError(f"malformed JSON: {exc}") from exc
    raise JsonExtractError(f"unbalanced JSON braces: {raw!r}")
