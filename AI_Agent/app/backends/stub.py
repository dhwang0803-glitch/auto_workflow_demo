"""StubLLMBackend — deterministic, network-free backend for local dev.

Copied from API_Server/app/services/ai_composer_service.py during the
AI_Agent split. Drives the AI Composer end-to-end without an Anthropic
key or a running llama-server. Selected via `LLM_BACKEND=stub`.

The response shape (JSON fence + `<rationale>`) matches what AIComposerService
expects, so the caller (API_Server AIComposerService) can parse it with no
special casing.
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator


class StubLLMBackend:
    async def complete(
        self,
        *,
        system: str,
        user_message: str,
        max_tokens: int,
    ) -> str:
        _, payload = self._decide(user_message)
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
