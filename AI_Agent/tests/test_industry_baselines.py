"""Unit tests for `app.services.industry_baselines.IndustryBaselinePool`.

Mirror of `test_personal_memory.py` for the PR-δ retrieval surface.
The pool is the read side of PLAN_15 PR-δ — `data/policies/{domain}.yaml`
is the canonical store, embeddings are computed at first load per
`(base_dir, domain)` and cached at the module level.

We lock in the same kind of cold-start contract as PR-γ: every error
mode resolves to an empty pool rather than an exception, because the
agent's regression guard depends on `pool.size == 0` being safe to
surface even when the YAML is missing or corrupt.

Embedding shape uses `StubEmbeddingBackend` (1024-dim, deterministic
SHA-256 expansion) so the tests run without torch / sentence-transformers
and the cosine ordering only depends on the SHA-256 of each input text.
For ranking arithmetic that needs explicit unit vectors, we direct-
construct `BaselineEntry` instances and skip the load path.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.backends.stub_embedding import StubEmbeddingBackend
from app.services.industry_baselines import (
    BaselineEntry,
    IndustryBaselinePool,
    _clear_cache_for_tests,
)


@pytest.fixture(autouse=True)
def _drop_cache() -> None:
    """Module-level cache uses `(base_dir, domain)` as the key. Pytest
    tmp_path is unique per test so cross-test contamination is
    impossible by construction, but a defensive clear keeps the test
    body's reasoning local — every test starts on a cold cache.
    """
    _clear_cache_for_tests()


def _write_yaml(
    base_dir: Path, domain: str, policies: list[dict[str, Any]]
) -> Path:
    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_dir / f"{domain}.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "domain": domain,
                "display_name": domain,
                "description": "test",
                "policies": policies,
            }
        ),
        encoding="utf-8",
    )
    return path


def _policy(
    *,
    policy_id: str,
    name: str = "test policy",
    condition: str = "Some condition.",
    action: str = "Some action.",
    sources: list[dict[str, str]] | None = None,
    source_kind: str = "synthesized",
) -> dict[str, Any]:
    return {
        "id": policy_id,
        "name": name,
        "condition": condition,
        "action": action,
        "rationale": "test",
        "sources": sources if sources is not None else [],
        "source_kind": source_kind,
    }


# --- load(): cold-start variants -----------------------------------------


@pytest.mark.asyncio
async def test_other_domain_returns_empty_pool(tmp_path: Path) -> None:
    """`domain="other"` is the safety-net category — no seed YAML
    exists, the pool is empty, and the agent's tool-registration check
    (`pool.size == 0`) keeps `search_industry_baselines` out of the
    catalog. The GitLab smoke baseline runs with default `domain="other"`,
    so this is the literal regression guard."""
    pool = await IndustryBaselinePool.load(
        str(tmp_path), "other", StubEmbeddingBackend()
    )
    assert pool.size == 0


@pytest.mark.asyncio
async def test_unseeded_domain_returns_empty_pool(tmp_path: Path) -> None:
    """A domain string outside the seeded whitelist is a defense-in-
    depth path: even if a future enum addition is missing a YAML
    fixture, the pool stays empty rather than raising."""
    # Cast around the type checker — at runtime `DomainCategory` is
    # just a Literal alias.
    pool = await IndustryBaselinePool.load(
        str(tmp_path),
        "made_up_domain",  # type: ignore[arg-type]
        StubEmbeddingBackend(),
    )
    assert pool.size == 0


@pytest.mark.asyncio
async def test_none_embedder_returns_empty_pool(tmp_path: Path) -> None:
    """The pool can't usefully serve `search` without an embedder — if
    upstream wiring forgot to pass one, decline gracefully. The route
    wires this from FastAPI Depends so the production path always has
    an embedder, but the unit boundary still checks."""
    _write_yaml(
        tmp_path, "ecommerce", [_policy(policy_id="ecommerce.test")]
    )
    pool = await IndustryBaselinePool.load(
        str(tmp_path), "ecommerce", embedding_backend=None
    )
    assert pool.size == 0


@pytest.mark.asyncio
async def test_missing_yaml_returns_empty_pool(tmp_path: Path) -> None:
    """A seeded domain whose YAML doesn't exist (e.g., test fixture
    isolation, partial migration). No exception, no log noise — empty."""
    pool = await IndustryBaselinePool.load(
        str(tmp_path), "ecommerce", StubEmbeddingBackend()
    )
    assert pool.size == 0


@pytest.mark.asyncio
async def test_malformed_yaml_returns_empty_pool_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A corrupt YAML file should not 502 the request. Operators get a
    warning in the log, the pool surfaces empty, the agent treats it as
    cold-start."""
    path = tmp_path / "ecommerce.yaml"
    path.write_text("policies: [not valid: : :", encoding="utf-8")

    with caplog.at_level(
        logging.WARNING, logger="app.services.industry_baselines"
    ):
        pool = await IndustryBaselinePool.load(
            str(tmp_path), "ecommerce", StubEmbeddingBackend()
        )
    assert pool.size == 0
    assert any(
        "industry_baselines" in rec.message and "ecommerce" in rec.message
        for rec in caplog.records
    )


@pytest.mark.asyncio
async def test_yaml_without_policies_returns_empty_pool(
    tmp_path: Path,
) -> None:
    """A YAML that is well-formed but missing the `policies:` list (or
    has it as an empty list) is still a valid no-op — same surface as
    a missing file."""
    _write_yaml(tmp_path, "ecommerce", [])
    pool = await IndustryBaselinePool.load(
        str(tmp_path), "ecommerce", StubEmbeddingBackend()
    )
    assert pool.size == 0


# --- load(): happy path --------------------------------------------------


@pytest.mark.asyncio
async def test_normal_yaml_loads_and_embeds(tmp_path: Path) -> None:
    """Two policies in the YAML → pool size 2, each entry embedded by
    the stub backend (1024-dim L2-normalized). The `policy_id` and
    `sources` fields round-trip from the YAML into the entry — both
    are part of the wire shape PLAN_13 §11.3 promised."""
    _write_yaml(
        tmp_path,
        "ecommerce",
        [
            _policy(
                policy_id="ecommerce.refund",
                name="Refund threshold",
                condition="Refunds above $500",
                action="Manager approval required",
                sources=[
                    {
                        "title": "Stripe — Refunds",
                        "url": "https://docs.stripe.com/refunds",
                    }
                ],
                source_kind="industry-baseline",
            ),
            _policy(
                policy_id="ecommerce.oos",
                name="Out-of-stock notification",
                condition="SKU unavailable",
                action="Notify within 4 hours",
                source_kind="synthesized",
            ),
        ],
    )

    pool = await IndustryBaselinePool.load(
        str(tmp_path), "ecommerce", StubEmbeddingBackend()
    )
    assert pool.size == 2

    # Search returns both entries; the cosine arithmetic with stub
    # embeddings is not semantically meaningful but the ordering is
    # deterministic for the same query, so we assert on the set rather
    # than the order.
    embed = StubEmbeddingBackend()
    query_vec = (await embed.embed(["refund approval threshold"]))[0]
    hits = pool.search(query_vec, k=2)
    assert {h.policy_id for h in hits} == {
        "ecommerce.refund",
        "ecommerce.oos",
    }
    refund_hit = next(h for h in hits if h.policy_id == "ecommerce.refund")
    assert refund_hit.sources == [
        {"title": "Stripe — Refunds", "url": "https://docs.stripe.com/refunds"}
    ]
    assert refund_hit.source_kind == "industry-baseline"


@pytest.mark.asyncio
async def test_load_caches_per_domain(tmp_path: Path) -> None:
    """Second load with the same `(base_dir, domain)` reuses the cached
    embedded entries — the embedder is NOT called again. We verify by
    counting calls on a recording embedder."""

    class CountingEmbedder(StubEmbeddingBackend):
        def __init__(self) -> None:
            self.calls = 0

        async def embed(self, texts: list[str]) -> list[list[float]]:
            self.calls += 1
            return await super().embed(texts)

    _write_yaml(
        tmp_path, "ecommerce", [_policy(policy_id="ecommerce.test")]
    )
    embedder = CountingEmbedder()

    pool_a = await IndustryBaselinePool.load(
        str(tmp_path), "ecommerce", embedder
    )
    pool_b = await IndustryBaselinePool.load(
        str(tmp_path), "ecommerce", embedder
    )
    assert pool_a.size == pool_b.size == 1
    # First call embeds; second call hits the cache and skips embed().
    assert embedder.calls == 1


@pytest.mark.asyncio
async def test_load_skips_malformed_entries(tmp_path: Path) -> None:
    """A YAML where one policy is missing a required field (e.g. no
    `condition`) should not poison the whole pool — drop the bad row,
    keep the rest. Resilience matters because seed YAMLs are hand-
    edited and a broken row should not blow up the wizard."""
    _write_yaml(
        tmp_path,
        "ecommerce",
        [
            _policy(policy_id="ecommerce.good"),
            {
                "id": "ecommerce.bad",
                "name": "Missing condition",
                # condition + action absent
            },
        ],
    )
    pool = await IndustryBaselinePool.load(
        str(tmp_path), "ecommerce", StubEmbeddingBackend()
    )
    assert pool.size == 1
    embed = StubEmbeddingBackend()
    hits = pool.search((await embed.embed(["anything"]))[0], k=5)
    assert [h.policy_id for h in hits] == ["ecommerce.good"]


# --- search(): ordering + filtering --------------------------------------


def test_search_orders_by_cosine_similarity() -> None:
    """Direct construction so the ranking arithmetic is the only
    variable. Unit vectors → cosine = dot product; the projection of
    each entry onto the query gives the expected order."""
    pool = IndustryBaselinePool(
        [
            BaselineEntry(
                policy_id="far",
                name="far",
                condition="",
                action="",
                sources=[],
                source_kind="synthesized",
                embedding=[0.0, 1.0, 0.0, 0.0],
            ),
            BaselineEntry(
                policy_id="exact",
                name="exact",
                condition="",
                action="",
                sources=[],
                source_kind="industry-baseline",
                embedding=[1.0, 0.0, 0.0, 0.0],
            ),
            BaselineEntry(
                policy_id="middle",
                name="middle",
                condition="",
                action="",
                sources=[],
                source_kind="synthesized",
                embedding=[0.7071, 0.7071, 0.0, 0.0],
            ),
        ]
    )
    hits = pool.search([1.0, 0.0, 0.0, 0.0], k=3)
    assert [h.policy_id for h in hits] == ["exact", "middle", "far"]


def test_search_drops_dimension_mismatch_entries() -> None:
    """If the embedder is upgraded mid-deploy and the cache holds 768-
    dim vectors while the live query is 1024-dim, the mismatched rows
    drop from comparison rather than 502-ing. `size` still counts them
    so the agent does not see a transient zero on partial migration."""
    pool = IndustryBaselinePool(
        [
            BaselineEntry(
                policy_id="old-dim",
                name="old",
                condition="",
                action="",
                sources=[],
                source_kind="synthesized",
                embedding=[1.0, 0.0, 0.0],
            ),
            BaselineEntry(
                policy_id="current-dim",
                name="current",
                condition="",
                action="",
                sources=[],
                source_kind="synthesized",
                embedding=[1.0, 0.0],
            ),
        ]
    )
    assert pool.size == 2
    hits = pool.search([1.0, 0.0], k=5)
    assert [h.policy_id for h in hits] == ["current-dim"]


def test_search_empty_pool_returns_empty_list() -> None:
    """The agent's tool-registration guard means this branch isn't
    reached in production, but a direct call must still return [] not
    crash."""
    pool = IndustryBaselinePool([])
    assert pool.search([1.0, 0.0], k=3) == []


def test_search_non_positive_k_returns_empty_list() -> None:
    """`k=0` is a valid call shape (the agent occasionally sends it
    while probing pool_size); k<0 is malformed but we coerce both to
    'no matches' rather than raising."""
    pool = IndustryBaselinePool(
        [
            BaselineEntry(
                policy_id="x",
                name="x",
                condition="",
                action="",
                sources=[],
                source_kind="synthesized",
                embedding=[1.0, 0.0],
            ),
        ]
    )
    assert pool.search([1.0, 0.0], k=0) == []
    assert pool.search([1.0, 0.0], k=-1) == []


# --- bundled fallback: empty base_dir uses the wizard's POLICIES_DIR ------


@pytest.mark.asyncio
async def test_empty_base_dir_falls_back_to_bundled() -> None:
    """`personal_memory_dir=""` was a 'feature disabled' signal in
    PR-γ; for industry baselines the symmetric default is the bundled
    `app/data/policies/` directory the wizard already loads from. The
    seed YAMLs ship with the image, so production runs with this
    fallback and never sets `INDUSTRY_BASELINE_DIR`. We assert that an
    empty base_dir DOES return a populated pool when domain is seeded.
    """
    pool = await IndustryBaselinePool.load(
        "", "ecommerce", StubEmbeddingBackend()
    )
    # The bundled ecommerce.yaml has 8 policies (PR #142 vintage). A
    # tighter assert risks coupling the test to YAML edits; the loose
    # assert just locks in "the fallback resolves to a populated pool."
    assert pool.size > 0
