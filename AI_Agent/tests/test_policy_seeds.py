"""Static validation of AI_Agent/data/policies/*.yaml seed files (PLAN_12 W2-3 + W2-4a).

These YAMLs are the reference set the gap_analyze service compares against
extracted/declared team skills. Format invariants here are load-bearing
for downstream prompts AND for the wizard's per-parameter rendering, so
we lock them with cheap structural assertions.

2026-04-28 polish (W2-4a) — schema migrated:
- `parameters` is a list of objects ({name, prompt, default_baseline,
  baseline_source}), not bare strings
- policy carries `sources: [{title, url}]` and `source_kind`
- `source_kind` is one of regulatory / industry-baseline / synthesized

W2-4d additions:
- every parameter must carry `help_text` (jargon explainer, 30-500 chars,
  2-3 sentences) and `example_answer` (one-line placeholder, 1-200 chars)
- the wire model treats both as optional (forward-compat for custom
  seeds), but the shipped seeds in this repo MUST fill them — that's the
  whole point of the polish C track (memory project_wizard_polish_abc.md)
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

POLICIES_DIR = Path(__file__).parent.parent / "data" / "policies"

EXPECTED_DOMAINS = {"ecommerce", "services", "consulting", "content", "nonprofit"}
REQUIRED_POLICY_FIELDS = {
    "id",
    "name",
    "condition",
    "action",
    "rationale",
    "parameters",
    "tags",
    "sources",
    "source_kind",
}
REQUIRED_PARAMETER_FIELDS = {
    "name",
    "prompt",
    "default_baseline",
    "baseline_source",
    "help_text",
    "example_answer",
}
ALLOWED_SOURCE_KINDS = {"regulatory", "industry-baseline", "synthesized"}

# W2-4d length bounds — keep wizard rendering predictable.
# help_text: jargon explainer, 2-3 sentences. Too short = useless tooltip;
# too long = breaks the inline help row in the wizard card.
HELP_TEXT_MIN, HELP_TEXT_MAX = 30, 500
# example_answer: one-line ghost-text placeholder shown inside the input.
EXAMPLE_ANSWER_MIN, EXAMPLE_ANSWER_MAX = 1, 200


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

        # parameters: list of objects with required fields
        assert isinstance(p["parameters"], list) and p["parameters"]
        seen_param_names: set[str] = set()
        for param in p["parameters"]:
            assert isinstance(param, dict), (
                f"{p['id']}: parameter must be an object, got {type(param).__name__}"
            )
            missing_fields = REQUIRED_PARAMETER_FIELDS - param.keys()
            assert not missing_fields, (
                f"{p['id']} param {param.get('name')!r}: missing {missing_fields}"
            )
            assert isinstance(param["name"], str) and param["name"].isupper(), (
                f"{p['id']}: parameter name must be UPPER_CASE, got {param['name']!r}"
            )
            assert param["name"] not in seen_param_names, (
                f"{p['id']}: duplicate parameter name {param['name']!r}"
            )
            seen_param_names.add(param["name"])
            for key in (
                "prompt",
                "default_baseline",
                "baseline_source",
                "help_text",
                "example_answer",
            ):
                assert isinstance(param[key], str) and param[key], (
                    f"{p['id']} {param['name']}: empty `{key}`"
                )
            help_len = len(param["help_text"])
            assert HELP_TEXT_MIN <= help_len <= HELP_TEXT_MAX, (
                f"{p['id']} {param['name']}: help_text length {help_len} "
                f"outside [{HELP_TEXT_MIN}, {HELP_TEXT_MAX}]"
            )
            example_len = len(param["example_answer"])
            assert EXAMPLE_ANSWER_MIN <= example_len <= EXAMPLE_ANSWER_MAX, (
                f"{p['id']} {param['name']}: example_answer length "
                f"{example_len} outside [{EXAMPLE_ANSWER_MIN}, "
                f"{EXAMPLE_ANSWER_MAX}]"
            )
            # example_answer is meant to be a single line — newlines break
            # the input ghost-text affordance.
            assert "\n" not in param["example_answer"], (
                f"{p['id']} {param['name']}: example_answer must be one line"
            )

        # sources: list (possibly empty) of {title, url}
        assert isinstance(p["sources"], list)
        for src in p["sources"]:
            assert isinstance(src, dict)
            assert isinstance(src.get("title"), str) and src["title"]
            assert isinstance(src.get("url"), str) and src["url"].startswith("http")

        # source_kind: enum
        assert p["source_kind"] in ALLOWED_SOURCE_KINDS, (
            f"{p['id']}: unknown source_kind {p['source_kind']!r}"
        )

        assert isinstance(p["tags"], list) and p["tags"]


def test_policy_ids_globally_unique() -> None:
    seen: dict[str, Path] = {}
    for path in _all_files():
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for p in doc["policies"]:
            assert p["id"] not in seen, f"{p['id']} also in {seen[p['id']]}"
            seen[p["id"]] = path


def test_industry_baseline_or_regulatory_policies_have_at_least_one_source() -> None:
    """If a policy claims `regulatory` or `industry-baseline`, it must
    actually link to at least one external source — that's the honesty
    contract from the polish redesign (memory project_wizard_polish_abc).
    `synthesized` policies legitimately have empty sources."""
    for path in _all_files():
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for p in doc["policies"]:
            if p["source_kind"] in {"regulatory", "industry-baseline"}:
                assert p["sources"], (
                    f"{p['id']}: source_kind={p['source_kind']} but no sources listed — "
                    "either add a real source or label as synthesized"
                )
