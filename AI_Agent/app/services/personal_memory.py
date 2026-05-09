"""Per-user personal-skill memory pool (PLAN_15 PR-γ AI_Agent half).

Path 1 of the personalization design (memory `project_personalization_memory_pattern.md`):
the canonical store for a user's personal skills is a per-user JSON
file on the Modal Volume — `{personal_memory_dir}/{user_id}.json`. The
schema mirrors `Database/migrations/...` PR #171's `skills` table but
the read path stays out of the SQL boundary so AI_Agent keeps the
"no Database direct calls" policy from `AI_Agent/CLAUDE.md`.

The pool is loaded ONCE per request (route entry) and shared across
every tool call the agent makes. Repeated `search_personal_skills`
invocations within one request hit the in-memory list — no file
reload, no DB query, sub-millisecond cosine across the small per-user
domain (typically O(10) skills).

Cold-start contract (the regression-guard knob): a missing or empty
file resolves to an empty pool. Callers check `pool.size == 0` to
short-circuit retrieval and feed the agent a `pool_size=0` observation
the system prompt teaches it to interpret as "skip retrieval, go
straight to extract_policies." Preserves the GitLab smoke baseline
(+3 cand vs single-shot) for `user_id=None` requests.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# Restrict user_id characters that may appear in a filename. The route
# hands us whatever string the caller put on the wire, so a hostile
# value like "../../etc/passwd" would otherwise resolve outside the
# memory dir. Allowed set is alnum + `_-.@`, which covers UUIDs, Auth0
# subjects, and email-like ids without opening path traversal.
_USER_ID_SAFE = re.compile(r"^[A-Za-z0-9_.@-]{1,128}$")


@dataclass(frozen=True)
class PersonalSkillEntry:
    """One row from the per-user JSON file.

    Mirrors the active fields of `skills` (PR #171 schema); deactivated
    rows are filtered out at load time so search and `size` only see
    the search domain. `embedding` is BGE-M3 1024-dim L2-normalized
    (cached at write time by the future PLAN_14 PR-D), so cosine
    similarity is just the dot product.
    """

    id: str
    condition: dict[str, Any]
    action: dict[str, Any]
    suggestion_hash: str
    embedding: list[float]
    source: str
    first_observed_at: str
    active: bool


class PersonalMemoryPool:
    """In-memory pool of one user's personal skills, loaded once per request."""

    def __init__(self, skills: list[PersonalSkillEntry]) -> None:
        self._skills = [s for s in skills if s.active]

    @classmethod
    def load(
        cls, base_dir: str | None, user_id: str | None
    ) -> "PersonalMemoryPool":
        """Load `{base_dir}/{user_id}.json` into a pool.

        Returns an empty pool on any of:
          - `base_dir` is None or empty string (memory feature disabled)
          - `user_id` is None or empty (anonymous request)
          - `user_id` contains unsafe characters (path traversal guard)
          - file does not exist (cold-start — first ever request for the user)
          - file is malformed JSON (logs a warning, degrades gracefully —
            a corrupt file should not 502 the request)

        The Path-1 design treats the file as canonical, so a graceful
        empty pool is the correct cold-start surface; the agent's system
        prompt knows `pool_size==0` means "skip retrieval, go straight
        to extract".
        """
        if not base_dir:
            return cls([])
        if not user_id:
            return cls([])
        if not _USER_ID_SAFE.match(user_id):
            logger.warning(
                "personal_memory: rejecting unsafe user_id %r — empty pool",
                user_id,
            )
            return cls([])

        path = os.path.join(base_dir, f"{user_id}.json")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except FileNotFoundError:
            return cls([])
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "personal_memory: failed to read %s (%s) — empty pool",
                path,
                exc,
            )
            return cls([])

        skills_raw = raw.get("skills", []) if isinstance(raw, dict) else []
        skills: list[PersonalSkillEntry] = []
        for item in skills_raw:
            entry = _entry_from_dict(item)
            if entry is not None:
                skills.append(entry)
        return cls(skills)

    @property
    def size(self) -> int:
        """Count of active skills — the search domain size.

        The agent uses this to decide whether to call the tool at all
        (cold-start = 0 → skip). It is also the `pool_size` field in
        the tool's return shape so the model can branch on the same
        signal without a second tool call.
        """
        return len(self._skills)

    def search(
        self, query_embedding: list[float], k: int
    ) -> list[PersonalSkillEntry]:
        """Return the top-k active skills by cosine similarity.

        Both query and stored vectors are expected L2-normalized
        (BGE-M3 default + StubEmbeddingBackend), so cosine similarity
        reduces to a dot product. Empty pool → empty result. `k <= 0`
        also returns empty (`k=0` is a legitimate "I just wanted the
        size" call shape, but the simpler contract is that any
        non-positive `k` means no matches).

        Skill entries with mismatched embedding dimension are silently
        dropped from the comparison — defensive for files written
        before a dimension migration. The pool's `size` still counts
        them so the agent does not see a transient zero.
        """
        if not self._skills or k <= 0 or not query_embedding:
            return []
        dim = len(query_embedding)
        scored: list[tuple[float, PersonalSkillEntry]] = []
        for entry in self._skills:
            if len(entry.embedding) != dim:
                continue
            scored.append((_dot(query_embedding, entry.embedding), entry))
        if not scored:
            return []
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [entry for _, entry in scored[:k]]


def _entry_from_dict(item: Any) -> PersonalSkillEntry | None:
    """Coerce one JSON record into a PersonalSkillEntry.

    Returns None for records that lack the minimum fields the search
    path needs (id, embedding). Soft fields (source, timestamps) fall
    back to safe defaults so a partially-written record still loads.
    """
    if not isinstance(item, dict):
        return None
    skill_id = item.get("id")
    embedding = item.get("embedding")
    if not isinstance(skill_id, str) or not skill_id:
        return None
    if not isinstance(embedding, list) or not embedding:
        return None
    if not all(isinstance(x, (int, float)) for x in embedding):
        return None

    condition = item.get("condition") or {}
    action = item.get("action") or {}
    if not isinstance(condition, dict) or not isinstance(action, dict):
        return None

    return PersonalSkillEntry(
        id=skill_id,
        condition=condition,
        action=action,
        suggestion_hash=str(item.get("suggestion_hash") or ""),
        embedding=[float(x) for x in embedding],
        source=str(item.get("source") or ""),
        first_observed_at=str(item.get("first_observed_at") or ""),
        active=bool(item.get("active", True)),
    )


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))
