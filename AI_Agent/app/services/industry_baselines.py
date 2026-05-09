"""Per-domain industry-baseline pool (PLAN_15 PR-δ AI_Agent half).

Reuses the existing seed YAMLs at `app/data/policies/{domain}.yaml` —
the same files that drive the Persona-A wizard's `gap_analyze` short-
circuit (memory `project_wizard_polish_abc.md`). Each policy carries
`name`, `condition`, `action`, `sources` (already WebFetch-validated in
PR #142), `source_kind` (regulatory / industry-baseline / synthesized).

The agent's `search_industry_baselines` tool surfaces top-k policies
similar to a chunk's main topic so the extractor has industry-standard
grounding to anchor its candidates against — matching the wire shape
PLAN_13 §11.3 promised: `[{policy_id, name, sources}]`.

Cold-start contract (the regression-guard knob): a `domain="other"`
request, an unknown domain, or a missing/malformed YAML resolves to
an empty pool. Callers check `pool.size == 0` to decline the tool
registration entirely — no `search_industry_baselines` in the catalog
means the agent's bit-level behavior is identical to PR-β/γ baseline,
preserving the GitLab smoke +3-cand recall delta.

Design notes (versus the per-user `PersonalMemoryPool`):

  - The YAML files are static and bundled into the image; embeddings
    are not pre-baked. We embed at first load per domain and cache the
    result for the process lifetime — the wizard hits ecommerce mostly,
    so the embed cost (~8 policies × 1024-dim BGE-M3) is paid once per
    container.
  - The cache key is `(base_dir, domain)` so test isolation (each
    pytest tmp_path) gets its own slot without leaking across runs.
  - No asyncio Lock around the cache — a duplicate first-load races
    only on cold-start, both producers compute identical entries
    (deterministic), and the wasted work is bounded to one domain's
    embedding pass. Avoiding the lock keeps the module event-loop
    agnostic, which simplifies pytest-asyncio teardown.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.backends.protocols import EmbeddingBackend
from app.models.domain import DomainCategory

logger = logging.getLogger(__name__)


# Whitelist matching DomainCategory's seeded values. "other" is the
# safety-net category and never has a seed YAML; restricting here is
# defense-in-depth even though `os.path.join` already prevents traversal
# below the base_dir.
_SEEDED_DOMAINS = frozenset(
    {"ecommerce", "services", "consulting", "content", "nonprofit"}
)


# Module-level cache. Key is (base_dir, domain) so different tmp_paths
# in tests get independent slots. Value is the embedded entries list —
# returning a new pool wrapping the same list is fine because the list
# is read-only in production (YAML doesn't change at runtime).
_EMBED_CACHE: dict[tuple[str, str], list["BaselineEntry"]] = {}


@dataclass(frozen=True)
class BaselineEntry:
    """One policy from a domain seed YAML, paired with its embedding.

    Mirrors the public surface PLAN_13 §11.3 promised
    (`policy_id, name, sources`) plus the fields the agent's prompt
    hint actually uses — `condition` and `action` carry the policy's
    operational text, `source_kind` lets the agent decide how strongly
    to lean on the suggestion (regulatory > industry-baseline >
    synthesized).
    """

    policy_id: str
    name: str
    condition: str
    action: str
    sources: list[dict[str, str]]
    source_kind: str
    embedding: list[float]


class IndustryBaselinePool:
    """In-memory pool of one domain's industry-baseline policies."""

    def __init__(self, entries: list[BaselineEntry]) -> None:
        self._entries = entries

    @classmethod
    async def load(
        cls,
        base_dir: str | None,
        domain: DomainCategory,
        embedding_backend: EmbeddingBackend | None,
    ) -> "IndustryBaselinePool":
        """Load `{base_dir}/{domain}.yaml` into an embedded pool.

        Returns an empty pool on any of:
          - `domain` is "other" or not in the seeded whitelist
          - `embedding_backend` is None (no way to embed queries later)
          - YAML file does not exist (e.g. unseeded test fixture path)
          - YAML is malformed (logs a warning, degrades gracefully —
            a corrupt seed must not 502 the request)
          - `policies:` list is empty / missing

        `base_dir=None` or `""` falls back to the bundled
        `app/data/policies/` directory the wizard already loads from
        (`services.skill_bootstrap.POLICIES_DIR`). Production runs with
        the default; tests pass a tmp_path with synthetic YAML.

        On the first call per `(base_dir, domain)` the entries are
        embedded and cached at the module level; subsequent calls
        return the cached entries — production cost is one BGE-M3 pass
        per domain per container lifetime.
        """
        if domain == "other" or domain not in _SEEDED_DOMAINS:
            return cls([])
        if embedding_backend is None:
            return cls([])

        if not base_dir:
            from app.services.skill_bootstrap import POLICIES_DIR

            base_dir = str(POLICIES_DIR)

        cache_key = (base_dir, domain)
        cached = _EMBED_CACHE.get(cache_key)
        if cached is not None:
            return cls(cached)

        entries = await _load_and_embed(base_dir, domain, embedding_backend)
        _EMBED_CACHE[cache_key] = entries
        return cls(entries)

    @property
    def size(self) -> int:
        """Count of indexed baseline policies — the search domain size.

        The agent uses this to decide whether to register the tool at
        all (cold-start = 0 → skip). Mirrored as `pool_size` in the
        tool's return shape so the model can branch on the same signal
        without a second tool call.
        """
        return len(self._entries)

    def search(
        self, query_embedding: list[float], k: int
    ) -> list[BaselineEntry]:
        """Return the top-k entries by cosine similarity.

        Both query and stored vectors are L2-normalized (BGE-M3 default
        + StubEmbeddingBackend), so cosine similarity reduces to a dot
        product. Empty pool → empty result. Non-positive `k` → empty.

        Entries with mismatched embedding dimension are silently
        dropped from the comparison — defensive across embedding-model
        upgrades; the pool's `size` still counts them so the agent
        does not see a transient zero on a partial migration.
        """
        if not self._entries or k <= 0 or not query_embedding:
            return []
        dim = len(query_embedding)
        scored: list[tuple[float, BaselineEntry]] = []
        for entry in self._entries:
            if len(entry.embedding) != dim:
                continue
            scored.append((_dot(query_embedding, entry.embedding), entry))
        if not scored:
            return []
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [entry for _, entry in scored[:k]]


async def _load_and_embed(
    base_dir: str, domain: str, embedder: EmbeddingBackend
) -> list[BaselineEntry]:
    """Read the YAML file and embed each policy's `name + condition +
    action` text. Returns an empty list (not raise) on any I/O or
    parse error so the route stays available for the cold-start path.
    """
    path = Path(base_dir) / f"{domain}.yaml"
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        logger.warning(
            "industry_baselines: failed to read %s (%s) — empty pool",
            path,
            exc,
        )
        return []

    try:
        doc = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        logger.warning(
            "industry_baselines: malformed YAML at %s (%s) — empty pool",
            path,
            exc,
        )
        return []

    policies = doc.get("policies") if isinstance(doc, dict) else None
    if not isinstance(policies, list) or not policies:
        return []

    # Build the text-to-embed list and the parallel scaffolds in one
    # pass so the embedding output indexes line up with the entries.
    texts: list[str] = []
    scaffolds: list[dict[str, Any]] = []
    for item in policies:
        if not isinstance(item, dict):
            continue
        policy_id = item.get("id")
        name = item.get("name")
        condition = item.get("condition")
        action = item.get("action")
        if not (
            isinstance(policy_id, str)
            and isinstance(name, str)
            and isinstance(condition, str)
            and isinstance(action, str)
        ):
            continue
        # Embed the human-readable triple. The agent's query is the
        # chunk's topic phrase; matching against the same surface form
        # the wizard uses for gap_analyze keeps the retrieval signal
        # consistent across both flows.
        texts.append(
            f"{name.strip()}. {condition.strip()} {action.strip()}"
        )
        sources_raw = item.get("sources") or []
        sources: list[dict[str, str]] = []
        for s in sources_raw if isinstance(sources_raw, list) else []:
            if isinstance(s, dict) and "title" in s and "url" in s:
                sources.append(
                    {"title": str(s["title"]), "url": str(s["url"])}
                )
        scaffolds.append(
            {
                "policy_id": policy_id,
                "name": name.strip(),
                "condition": condition.strip(),
                "action": action.strip(),
                "sources": sources,
                "source_kind": str(item.get("source_kind") or ""),
            }
        )

    if not texts:
        return []

    vectors = await embedder.embed(texts)
    if len(vectors) != len(scaffolds):
        # Shouldn't happen with the protocol contract, but if a backend
        # returns a partial list we degrade to empty rather than zip
        # mismatched data — a misordered match is worse than no match.
        logger.warning(
            "industry_baselines: embedding count mismatch for %s "
            "(%d texts → %d vectors) — empty pool",
            domain,
            len(texts),
            len(vectors),
        )
        return []

    return [
        BaselineEntry(
            policy_id=scaffold["policy_id"],
            name=scaffold["name"],
            condition=scaffold["condition"],
            action=scaffold["action"],
            sources=scaffold["sources"],
            source_kind=scaffold["source_kind"],
            embedding=[float(x) for x in vec],
        )
        for scaffold, vec in zip(scaffolds, vectors)
    ]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _clear_cache_for_tests() -> None:
    """Invalidate the module-level embedded-entries cache.

    Test-only escape hatch — pytest tmp_path is unique per test so
    cross-test contamination is impossible by construction, but a test
    that mutates a YAML mid-test and reloads with the same path needs
    a way to drop the stale entry. Production never calls this.
    """
    _EMBED_CACHE.clear()
