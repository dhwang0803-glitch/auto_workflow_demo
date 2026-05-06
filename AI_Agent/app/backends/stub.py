"""StubLLMBackend — deterministic, network-free backend for local dev.

Drives both the AI Composer (PLAN_02) and the skill-bootstrap interview
(PLAN_12) without an Anthropic key or a running llama-server. Selected
via `LLM_BACKEND=stub`.

Branching is by **system prompt first line**, which is the only stable
distinguishing marker across the call types we serve:

| First line contains... | Path |
|------------------------|------|
| "domain classifier" | classify_domain (W2-2) |
| "gap analyzer" | gap_analyze (W2-4b, coverage-only) |
| "answer-to-skill compiler" | answers_to_skill (W2-4c, batch) and the legacy single-shot wrapper |
| "policy extractor" | policy_extract (W3-4, docs path) |
| (anything else) | AI Composer compose (PLAN_02) |

Each path returns a JSON shape that the matching parser in
`services/skill_bootstrap.py` / `services/domain_classifier.py` /
`API_Server/.../ai_composer_service.py` accepts unchanged. Mock data is
pulled from the same `data/policies/*.yaml` seeds the live LLM is
prompted with, so a Persona A walkthrough on stub mode mirrors the live
flow without hitting Modal.

2026-04-28 polish: gap_analyze service now returns deterministically when
extracted_skills is empty (no LLM call). The stub's gap_analyze branch
only fires for the non-empty case (Persona B-style). It returns a
coverage-only shape (`{missing_policy_ids: [...]}`) — the service does
all per-parameter / sources / source_kind enrichment from the seed.
"""
from __future__ import annotations

import asyncio
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import AsyncIterator

import yaml

POLICIES_DIR = Path(__file__).parent.parent / "data" / "policies"

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
        images: list[str] | None = None,
    ) -> str:
        # Stub doesn't reason about images — the deterministic branches
        # already cover every prompt shape. Accepted for Protocol parity.
        del images
        first_line = (system or "").lstrip().splitlines()[0] if system else ""
        if "domain classifier" in first_line:
            return self._classify_response(user_message)
        if "gap analyzer" in first_line:
            return self._gap_analyze_response(system)
        if "answer-to-skill compiler" in first_line:
            return self._answer_to_skill_response(system, user_message)
        if "policy extractor" in first_line:
            return self._policy_extract_response(user_message)

        # AI Composer path (PLAN_02) — preserved verbatim.
        _, payload = self._decide(user_message)
        return "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"

    async def stream(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
        images: list[str] | None = None,
    ) -> AsyncIterator[str]:
        # Streaming is only used by AI Composer. Skill-bootstrap callers go
        # through complete(). We keep the pre-existing PLAN_02 stream path.
        del images
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
        # Coverage-only shape: just policy_ids. The service enriches with
        # parameters/sources/source_kind from the seed YAML — the live LLM
        # never sees those fields, and neither do we.
        #
        # Hits this branch only when the service couldn't short-circuit
        # (i.e. extracted_skills was non-empty). For the demo case we
        # claim every seed policy is still missing — the stub does not
        # reason about coverage.
        m = re.search(r"domain as `([a-z_]+)`", system or "")
        domain = m.group(1) if m else "other"
        seeds = _stub_seeds().get(domain, [])
        # Cap at 5 so a Persona B-style walkthrough on the stub keeps the
        # interview length predictable in tests / scripted demos.
        missing_ids = [seed["id"] for seed in seeds[:5]]
        return json.dumps({"missing_policy_ids": missing_ids})

    def _answer_to_skill_response(self, system: str, user_message: str) -> str:
        # New batch user_message format from services.answers_to_skill:
        #   "Per-parameter answers:\n- <param>: question=..., answer='...'\n..."
        # Legacy single-shot wrapper still routes through the same prompt
        # by passing one parameter, so the same stub serves both.
        id_match = re.search(r"Source policy template \(([^)]+)\)", system or "")
        policy_id = id_match.group(1) if id_match else "unknown.policy"
        name_match = re.search(r"^- name:\s*(.+)$", system or "", flags=re.MULTILINE)
        policy_phrase = (
            name_match.group(1).strip().rstrip(".").lower()
            if name_match
            else policy_id
        )

        # Extract every "answer='...'" so the stub can:
        #   1) detect if any answer triggers needs_clarification,
        #   2) embed the user values into the synthesized condition/action
        #      so tests can assert "$200" appears in the draft.
        answer_blobs = re.findall(r"answer='([^']*)'", user_message or "")
        if not answer_blobs:
            # Fallback for tests that pass a freeform user_message.
            answer_blobs = [user_message.strip()] if user_message else []

        clarify = any(
            len(a) < 4
            or a.endswith("?")
            or any(p in a.lower() for p in ("i don't know", "depends", "not sure"))
            for a in answer_blobs
        )
        hint = (
            f"Could you give a specific rule or value for {policy_phrase}?"
            if clarify
            else ""
        )

        # Substituted-values surface: join the blobs so a test asserting
        # `"$200" in draft.condition` keeps passing across single + batch.
        values = "; ".join(answer_blobs) if answer_blobs else "(empty)"
        draft = {
            "name": f"{policy_id}_skill",
            "description": f"Stubbed skill for {policy_id}.",
            "condition": f"policy:{policy_id} with values [{values}]",
            "action": f"apply values [{values}] per team's {policy_phrase}",
            "rationale": "Stubbed — set per team's standard.",
            "needs_clarification": clarify,
            "clarification_hint": hint,
        }
        return json.dumps(draft, ensure_ascii=False)

    def _policy_extract_response(self, user_message: str) -> str:
        """Heuristic: surface 0-2 candidate skills based on cheap text signals.

        We're stubbing what a real LLM would do — not faking domain
        understanding — so the rules are intentionally crude:

        - chunk mentions a dollar threshold (`$NNN`) → emit a "threshold
          escalation" candidate using the matched figure.
        - chunk mentions an action verb against a target (escalate / page /
          notify / route / approve) → emit a "workflow trigger" candidate
          quoting the surrounding clause.
        - vague signals like "be careful with PII" / "handle securely" →
          emit ONE candidate with needs_clarification=true so the W3-7
          review UI can exercise that path.
        - otherwise return an empty list (the chunk has no policy).
        """
        text = (user_message or "").strip()
        if not text:
            return json.dumps({"candidates": []}, ensure_ascii=False)

        candidates: list[dict] = []

        # Threshold pattern — first dollar amount in the chunk.
        money = re.search(r"\$\s?(\d[\d,]*)", text)
        if money:
            amount = money.group(0).replace(" ", "")
            candidates.append(
                {
                    "name": f"Escalate amounts over {amount}",
                    "description": (
                        f"Stubbed extractor candidate: requests above "
                        f"{amount} need a manager."
                    ),
                    "condition": (
                        f"A request mentions an amount greater than {amount}"
                    ),
                    "action": "Route to a manager for explicit approval before responding",
                    "rationale": "Stubbed — lifted from chunk's threshold mention.",
                    "needs_clarification": False,
                    "clarification_hint": "",
                }
            )

        # Workflow trigger pattern — verb + object phrase.
        action_match = re.search(
            r"\b(escalate|page|notify|route|approve|reject|forward)\b[^.]*",
            text,
            flags=re.IGNORECASE,
        )
        if action_match:
            phrase = action_match.group(0).strip().rstrip(",;")
            verb = action_match.group(1).lower()
            candidates.append(
                {
                    "name": f"{verb.capitalize()} per chunk policy",
                    "description": f"Stubbed extractor candidate around '{verb}' verb.",
                    "condition": f"The situation described in: \"{phrase[:120]}\"",
                    "action": f"Apply the chunk-specified action: {phrase[:120]}",
                    "rationale": "Stubbed — chunk verb pattern matched.",
                    "needs_clarification": False,
                    "clarification_hint": "",
                }
            )

        # Vague-signal pattern — emit ONE clarification-needed candidate so
        # tests can assert the needs_clarification path even on stub.
        vague_terms = ("pii", "be careful", "handle securely", "as needed")
        text_lower = text.lower()
        if not candidates and any(t in text_lower for t in vague_terms):
            candidates.append(
                {
                    "name": "Vague handling policy (needs clarification)",
                    "description": "Stubbed extractor: chunk uses vague guidance.",
                    "condition": "Chunk mentions a sensitive topic without naming it",
                    "action": "Apply team's handling rule once defined",
                    "rationale": "Stubbed — vague chunk surface hit.",
                    "needs_clarification": True,
                    "clarification_hint": (
                        "What specifically counts as the sensitive item, and "
                        "what action is required?"
                    ),
                }
            )

        return json.dumps({"candidates": candidates}, ensure_ascii=False)

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
