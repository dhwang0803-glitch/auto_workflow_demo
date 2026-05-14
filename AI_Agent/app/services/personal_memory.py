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

PR-I added the write side (`upsert_personal_skill`) — API_Server's
`activate_candidate` calls into AI_Agent which lands here. The write
path mirrors the read path's user_id sanitization so callers cannot
escape the base directory regardless of which side originated the id.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

PERSONAL_MEMORY_VOLUME_NAME = "agent-personal-memory"


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


# --- writer (PR-I) -------------------------------------------------------


class PersonalMemoryWriteError(Exception):
    """Raised when the per-user JSON file cannot be persisted.

    Surfaces as 5xx at the route. Distinct from the load-side soft
    failures because a write loss is not silently recoverable — the
    caller (API_Server activate_candidate) needs to know.
    """


def _safe_user_path(base_dir: str, user_id: str) -> str:
    """Return `{base_dir}/{user_id}.json`, raising on traversal.

    Mirrors the load() guard so the write path can't be tricked into
    overwriting (or creating) files outside the memory dir even when
    the caller is a privileged API_Server.
    """
    if not base_dir:
        raise PersonalMemoryWriteError("personal_memory_dir is not configured")
    if not user_id or not _USER_ID_SAFE.match(user_id):
        raise PersonalMemoryWriteError(f"unsafe user_id {user_id!r}")
    return os.path.join(base_dir, f"{user_id}.json")


def _read_existing(path: str, user_id: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        data = {}
    except (OSError, json.JSONDecodeError) as exc:
        # Quarantine the malformed file so the next read doesn't keep
        # tripping the same warning. Then start fresh — losing the bad
        # blob is preferable to refusing all future writes for the user.
        logger.warning(
            "personal_memory: file %s unreadable (%s) — quarantining and restarting",
            path,
            exc,
        )
        try:
            os.replace(path, f"{path}.corrupt")
        except OSError:
            pass
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("user_id", user_id)
    data.setdefault("version", "v1")
    data.setdefault("skills", [])
    data.setdefault("reviews", [])
    if not isinstance(data["skills"], list):
        data["skills"] = []
    return data


async def _commit_volume_best_effort() -> None:
    """Push the just-written file to other Modal containers.

    Modal Volumes only sync across containers on `commit()`; without
    this the next reflective-extract request that lands on a different
    warm container would still see the pre-write file and the personal
    skill would not surface. No-op when the Modal SDK isn't installed
    (pytest) or when the volume name isn't published yet (local dev).

    Uses Modal's async `.aio()` shim when available so we don't block
    the event loop for the network-bound commit; falls back to a
    thread-offload of the sync API for older Modal versions.
    """
    try:
        import modal  # type: ignore
    except ImportError:
        return
    try:
        vol = modal.Volume.from_name(PERSONAL_MEMORY_VOLUME_NAME)
        commit_aio = getattr(vol.commit, "aio", None)
        if commit_aio is not None:
            await commit_aio()
        else:
            import asyncio

            await asyncio.to_thread(vol.commit)
    except Exception as exc:  # noqa: BLE001 — Modal raises a tree of errors
        logger.warning(
            "personal_memory: volume commit skipped (%s)", exc
        )


async def upsert_personal_skill(
    *,
    base_dir: str,
    user_id: str,
    entry: PersonalSkillEntry,
) -> int:
    """Append or update one skill entry in the user's JSON file.

    Idempotent on `entry.id`: when the id already exists the row is
    replaced (so re-activating after an edit produces one row, not two).
    Returns the new active-skill count so the caller can include it in
    the response without a follow-up read.

    Persists via tmp-file + `os.replace`, atomic on both POSIX and
    Windows (Python 3.3+). Commits the Modal Volume after rename so
    other warm containers see the new row on their next request.
    """
    path = _safe_user_path(base_dir, user_id)
    os.makedirs(base_dir, exist_ok=True)
    data = _read_existing(path, user_id)

    new_row = {
        "id": entry.id,
        "condition": entry.condition,
        "action": entry.action,
        "suggestion_hash": entry.suggestion_hash,
        "embedding": entry.embedding,
        "source": entry.source,
        "first_observed_at": entry.first_observed_at,
        "active": entry.active,
    }
    skills: list[dict[str, Any]] = data["skills"]
    replaced = False
    for i, existing in enumerate(skills):
        if isinstance(existing, dict) and existing.get("id") == entry.id:
            skills[i] = new_row
            replaced = True
            break
    if not replaced:
        skills.append(new_row)

    data["updated_at"] = datetime.now(timezone.utc).isoformat()

    # Write to a tmp file in the same directory so `os.replace` is on
    # the same filesystem (rename across mounts isn't atomic).
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{user_id}.", suffix=".json.tmp", dir=base_dir
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp_path, path)
    except OSError as exc:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise PersonalMemoryWriteError(
            f"failed to persist {path}: {exc}"
        ) from exc

    await _commit_volume_best_effort()

    return sum(1 for s in skills if isinstance(s, dict) and s.get("active"))
