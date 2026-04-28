"""Static validation of AI_Agent/data/policies/*.yaml seed files (PLAN_12 W2-3).

These YAMLs are the reference set the gap_analyze LLM (W2-4) compares
against extracted/declared team skills. Format invariants here are load-
bearing for downstream prompts, so we lock them with cheap structural
assertions rather than trusting a free-form schema.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

POLICIES_DIR = Path(__file__).parent.parent / "data" / "policies"

EXPECTED_DOMAINS = {"ecommerce", "services", "consulting", "content", "nonprofit"}
REQUIRED_POLICY_FIELDS = {"id", "name", "condition", "action", "rationale", "parameters", "tags"}


def _all_files() -> list[Path]:
    return sorted(POLICIES_DIR.glob("*.yaml"))


def test_expected_five_domain_files_exist() -> None:
    files = _all_files()
    domains_on_disk = {f.stem for f in files}
    assert domains_on_disk == EXPECTED_DOMAINS


@pytest.mark.parametrize("path", _all_files(), ids=lambda p: p.stem)
def test_policy_file_parses_and_has_required_shape(path: Path) -> None:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert doc["domain"] == path.stem, "domain field must match filename"
    assert isinstance(doc.get("display_name"), str) and doc["display_name"]
    assert isinstance(doc.get("description"), str) and doc["description"]

    policies = doc.get("policies")
    assert isinstance(policies, list)
    # Resume note: 5-10 policies per domain
    assert 5 <= len(policies) <= 10, f"{path.stem}: {len(policies)} policies"

    seen_ids: set[str] = set()
    for p in policies:
        missing = REQUIRED_POLICY_FIELDS - p.keys()
        assert not missing, f"{path.stem} {p.get('id')}: missing {missing}"

        # id must be domain-namespaced and unique within file
        assert p["id"].startswith(f"{path.stem}."), p["id"]
        assert p["id"] not in seen_ids, f"duplicate id {p['id']}"
        seen_ids.add(p["id"])

        assert isinstance(p["parameters"], list) and p["parameters"]
        assert all(isinstance(x, str) and x.isupper() for x in p["parameters"]), p["id"]
        assert isinstance(p["tags"], list) and p["tags"]


def test_policy_ids_globally_unique() -> None:
    seen: dict[str, Path] = {}
    for path in _all_files():
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for p in doc["policies"]:
            assert p["id"] not in seen, f"{p['id']} also in {seen[p['id']]}"
            seen[p["id"]] = path
