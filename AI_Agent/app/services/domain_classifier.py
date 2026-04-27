"""Domain classifier (PLAN_12 W2-2).

Wraps an LLMBackend.complete call with the classifier prompt and parses the
JSON response. The classifier kicks off the Persona A wizard — once the
domain is known, the gap_analyze step (W2-4) loads the matching seed YAML
from data/policies/ and turns its `parameters` lists into wizard questions.

Single-shot, no streaming, no multi-turn — classification fires once at the
start of the interview. Prompt is small (~250 tokens) and deterministic at
temperature 0, so the worst case fits comfortably under the multi-turn
budget defined in ADR-022.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import get_args

import yaml

from app.backends.protocols import LLMBackend
from app.models.domain import DomainCategory, DomainClassification

POLICIES_DIR = Path(__file__).parent.parent.parent / "data" / "policies"
DOMAIN_MAX_TOKENS = 256

# Single source of truth for "other" — present in DomainCategory but absent
# from the seed YAMLs (no policies for it; the wizard handles it specially).
_OTHER_DOMAIN = "other"


class ClassifierParseError(ValueError):
    """LLM response could not be parsed into a DomainClassification."""


@lru_cache(maxsize=1)
def _seed_descriptions() -> list[tuple[str, str, str]]:
    """Return [(domain, display_name, description), ...] from seed YAMLs.

    The classifier prompt is built from these so that adding a new domain
    means dropping a new YAML — no prompt edit needed (subject to also
    updating DomainCategory in models/domain.py).
    """
    out: list[tuple[str, str, str]] = []
    for path in sorted(POLICIES_DIR.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        out.append((doc["domain"], doc["display_name"], doc["description"].strip()))
    return out


@lru_cache(maxsize=1)
def _classifier_system_prompt() -> str:
    lines = [
        "You are a domain classifier for a workflow-automation product. "
        "Your job is to classify the user's free-text description of their "
        "business into exactly one of the categories listed below.",
        "",
        "Categories:",
    ]
    for domain, display_name, description in _seed_descriptions():
        lines.append(f"- {domain} ({display_name}): {description}")
    lines.append(
        f"- {_OTHER_DOMAIN} (None of the above): the user clearly does not "
        "fit any category above (e.g. internal tooling team, manufacturing, "
        "research lab, government agency)."
    )
    lines.extend(
        [
            "",
            "Output ONLY a single JSON object. No prose, no markdown fences. "
            "Schema:",
            '  {"domain": "<category>", "confidence": <float 0..1>, '
            '"rationale": "<one short sentence>"}',
            "",
            "Rules:",
            "- `domain` MUST be one of: "
            + ", ".join(d for d, _, _ in _seed_descriptions())
            + f", {_OTHER_DOMAIN}.",
            "- If the input is ambiguous between two categories, pick the "
            "closer match and lower the confidence accordingly (e.g. 0.55).",
            f"- Use `{_OTHER_DOMAIN}` only when no category clearly applies. "
            "Do not default to it for unclear-but-plausible inputs.",
            "- Keep rationale to one short sentence (≤ 25 words).",
        ]
    )
    return "\n".join(lines)


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _extract_json(raw: str) -> dict:
    """Pull the JSON object out of a model response.

    Tolerates ```json fences (Anthropic) and stray prose before/after the
    object (small models). Returns the first parseable {...} block.
    """
    text = raw.strip()
    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()

    # Find the first balanced top-level {...}.
    start = text.find("{")
    if start == -1:
        raise ClassifierParseError(f"no JSON object in response: {raw!r}")
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
                    raise ClassifierParseError(
                        f"malformed JSON: {exc}"
                    ) from exc
    raise ClassifierParseError(f"unbalanced JSON braces: {raw!r}")


def _parse_response(raw: str) -> DomainClassification:
    body = _extract_json(raw)

    domain = body.get("domain")
    valid_domains = set(get_args(DomainCategory))
    if domain not in valid_domains:
        raise ClassifierParseError(
            f"domain {domain!r} not in {sorted(valid_domains)}"
        )

    confidence = body.get("confidence")
    if not isinstance(confidence, (int, float)):
        raise ClassifierParseError(
            f"confidence must be number, got {type(confidence).__name__}"
        )
    confidence = max(0.0, min(1.0, float(confidence)))

    rationale = body.get("rationale", "")
    if not isinstance(rationale, str):
        rationale = str(rationale)

    return DomainClassification(
        domain=domain,
        confidence=confidence,
        rationale=rationale.strip(),
    )


async def classify_domain(backend: LLMBackend, text: str) -> DomainClassification:
    """Run the classifier against the active LLMBackend."""
    raw = await backend.complete(
        system=_classifier_system_prompt(),
        user_message=text.strip(),
        max_tokens=DOMAIN_MAX_TOKENS,
    )
    return _parse_response(raw)
