"""StubLLMBackend — deterministic, network-free backend for local dev.

Drives both the AI Composer (PLAN_02) and the skill-bootstrap interview
(PLAN_12) without an Anthropic key or a running llama-server. Selected
via `LLM_BACKEND=stub`.

Branching is by **system prompt first line**, which is the only stable
distinguishing marker across the four call types we serve:

| First line contains... | Path |
|------------------------|------|
| "domain classifier" | classify_domain (W2-2) |
| "gap analyzer" | gap_analyze (W2-4) |
| "answer-to-skill compiler" | answer_to_skill (W2-4) |
| (anything else) | AI Composer compose (PLAN_02) |

Each path returns a JSON shape that the matching parser in
`services/skill_bootstrap.py` / `services/domain_classifier.py` /
`API_Server/.../ai_composer_service.py` accepts unchanged. Mock data is
pulled from the same `data/policies/*.yaml` seeds the live LLM is
prompted with, so a Persona A walkthrough on stub mode mirrors the live
flow without hitting Modal.
"""
from __future__ import annotations

import asyncio
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import AsyncIterator

import yaml

POLICIES_DIR = Path(__file__).parent.parent.parent / "data" / "policies"

# Coarse keyword → domain map for the stub classifier. Hand-tuned against
# the seed YAML descriptions; matches are checked in order so the first
# hit wins (important for "consulting agency" → consulting, not services).
_DOMAIN_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("ecommerce", ("ecommerce", "e-commerce", "online store", "shop", "retail", "checkout", "shopify")),
    ("nonprofit", ("nonprofit", "non-profit", "charity", "donor", "ngo", "foundation")),
    ("consulting", ("consulting", "consultant", "advisory", "advisor", "engagement")),
    ("content", ("content", "blog", "newsletter", "podcast", "youtube", "creator", "publishing")),
    ("services", ("agency", "service", "salon", "studio", "clinic", "appointment", "booking")),
]

_DOMAIN_RATIONALES = {
    "ecommerce": "Mentions online retail / checkout signals.",
    "services": "Mentions services / appointments / agency work.",
    "consulting": "Mentions consulting / advisory engagement.",
    "content": "Mentions content / publishing creator workflow.",
    "nonprofit": "Mentions nonprofit / donor / charity work.",
    "other": "No clear match to seeded categories.",
}


@lru_cache(maxsize=1)
def _stub_seeds() -> dict[str, list[dict]]:
    """Same loader shape as services/skill_bootstrap._seeds_by_domain.

    We re-implement here instead of importing from services to keep the
    backend layer self-contained (services depend on backends, not the
    other way around).
    """
    out: dict[str, list[dict]] = {}
    for path in sorted(POLICIES_DIR.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        out[doc["domain"]] = doc["policies"]
    return out


class StubLLMBackend:
    async def complete(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
    ) -> str:
        first_line = (system or "").lstrip().splitlines()[0] if system else ""
        if "domain classifier" in first_line:
            return self._classify_response(user_message)
        if "gap analyzer" in first_line:
            return self._gap_analyze_response(system)
        if "answer-to-skill compiler" in first_line:
            return self._answer_to_skill_response(system, user_message)

        # AI Composer path (PLAN_02) — preserved verbatim.
        _, payload = self._decide(user_message)
        return "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"

    async def stream(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        # Streaming is only used by AI Composer. Skill-bootstrap callers go
        # through complete(). We keep the pre-existing PLAN_02 stream path.
        _, payload = self._decide(user_message)
        rationale = payload.get("rationale", "")
        yield "<rationale>"
        for i in range(0, len(rationale), 8):
            await asyncio.sleep(0.04)
            yield rationale[i : i + 8]
        yield "</rationale>\n```json\n"
        yield json.dumps(payload, ensure_ascii=False)
        yield "\n```"

    async def ready(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None

    # ---- skill-bootstrap stubs --------------------------------------------

    def _classify_response(self, user_message: str) -> str:
        text = (user_message or "").lower()
        domain = "other"
        for candidate, keywords in _DOMAIN_KEYWORDS:
            if any(k in text for k in keywords):
                domain = candidate
                break
        return json.dumps(
            {
                "domain": domain,
                "confidence": 0.85 if domain != "other" else 0.4,
                "rationale": _DOMAIN_RATIONALES[domain],
            }
        )

    def _gap_analyze_response(self, system: str) -> str:
        # Pull the active domain straight out of the prompt. The live
        # services/skill_bootstrap module formats it as `domain as `<x>``,
        # which is the only stable place the value appears verbatim.
        m = re.search(r"domain as `([a-z_]+)`", system or "")
        domain = m.group(1) if m else "other"
        seeds = _stub_seeds().get(domain, [])

        # For Persona A demo (docs-empty start) the wizard is most
        # interesting with ~5 missing policies. Cap at 5 for any domain
        # so the interview length stays predictable in tests/scripted
        # walkthroughs. Domains with fewer seeds (none currently) just
        # surface what they have.
        #
        # Question text mirrors what the live gap_analyze prompt asks the
        # LLM to produce: plain-language phrasing that does NOT echo the
        # raw `parameters` list (e.g. ask "policy on refund threshold
        # escalation" not "What is your REFUND_AUTO_APPROVE_LIMIT?"). The
        # `parameter` field still carries the canonical name so downstream
        # consumers can align answers to the seed schema.
        missing = []
        for seed in seeds[:5]:
            params = seed.get("parameters") or []
            primary = params[0] if params else None
            policy_phrase = seed["name"].rstrip(".").lower()
            question_text = (
                f"What is your team's policy on {policy_phrase}?"
            )
            missing.append(
                {
                    "policy_id": seed["id"],
                    "questions": [
                        {"text": question_text, "parameter": primary}
                    ],
                }
            )

        return json.dumps({"missing": missing})

    def _answer_to_skill_response(self, system: str, user_message: str) -> str:
        # System prompt header includes `## Source policy template (X.Y)`
        # plus a `- name: <human title>` line. The id stays as the technical
        # anchor for condition/name fields; the human-readable name is what
        # the user sees on the clarification hint so we mirror live LLM
        # phrasing instead of leaking dotted ids into the UI.
        id_match = re.search(r"Source policy template \(([^)]+)\)", system or "")
        policy_id = id_match.group(1) if id_match else "unknown.policy"
        name_match = re.search(r"^- name:\s*(.+)$", system or "", flags=re.MULTILINE)
        policy_phrase = (
            name_match.group(1).strip().rstrip(".").lower()
            if name_match
            else policy_id
        )

        # User message is "Question: ...\nAnswer: ...". Pull the answer
        # text so the stub can decide whether to flag clarification.
        answer_match = re.search(r"Answer:\s*(.+)", user_message or "", flags=re.DOTALL)
        answer = answer_match.group(1).strip() if answer_match else ""

        # ADR-022 §8.2: needs_clarification when answer is non-actionable.
        # Heuristic the stub uses (deterministic for tests):
        #   - ends with "?", or
        #   - contains "I don't know" / "depends" / "not sure", or
        #   - is shorter than 4 chars after stripping
        clarify = (
            len(answer) < 4
            or answer.endswith("?")
            or any(p in answer.lower() for p in ("i don't know", "depends", "not sure"))
        )
        hint = (
            f"Could you give a specific rule or value for {policy_phrase}?"
            if clarify
            else ""
        )

        draft = {
            "name": f"{policy_id}_skill",
            "description": f"Stubbed skill for {policy_id}.",
            "condition": f"policy:{policy_id}",
            "action": f"value:{answer or '(empty)'}",
            "rationale": "Stubbed — set per team's standard.",
            "needs_clarification": clarify,
            "clarification_hint": hint,
        }
        return json.dumps(draft, ensure_ascii=False)

    # ---- AI Composer (PLAN_02) — unchanged ---------------------------------

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
                "then email it to the team. Configure LLM_BACKEND=anthropic "
                "or llamacpp for real model responses."
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
