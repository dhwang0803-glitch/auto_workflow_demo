"""Registry completeness — guards against the bug where src/nodes/__init__.py
was empty, causing Worker processes to load zero node types on startup (unit
tests pre-imported specific modules so they missed this).

Any new node added MUST extend src/nodes/__init__.py. This test fails
if a file in src/nodes/ (not a test fixture) isn't wired there.
"""
from __future__ import annotations

from pathlib import Path


def test_registry_contains_all_node_files():
    # Trigger package import — this is what Worker startup does.
    import src.nodes  # noqa: F401
    from src.nodes.registry import registry

    nodes_dir = Path(__file__).resolve().parents[1] / "src" / "nodes"
    # google_workspace.py is the shared ADR-019 base class, not a concrete
    # node — its subclasses (gmail_send.py etc.) register themselves.
    excluded = {"__init__.py", "base.py", "registry.py", "google_workspace.py"}
    node_files = {
        p.stem for p in nodes_dir.glob("*.py") if p.name not in excluded
    }

    registered = set(registry.list_types())

    # Every node .py file must register at least one type. File stem
    # usually matches node_type but not always (e.g. slack.py -> slack_notify).
    # So we check the registry is at least as large as the file count.
    assert len(registered) >= len(node_files), (
        f"registry has {len(registered)} types "
        f"but nodes/ has {len(node_files)} files — "
        f"did you forget to import a new node in src/nodes/__init__.py? "
        f"files: {sorted(node_files)}, registered: {sorted(registered)}"
    )


def test_adr_017_minimum_catalog_registered():
    """ADR-017 locks the 21-node minimum catalog. Must all be reachable."""
    import src.nodes  # noqa: F401
    from src.nodes.registry import registry

    expected = {
        # Flow / Logic (5)
        "condition", "code", "delay", "loop_items", "merge",
        # Data Transform (2)
        "transform", "filter",
        # HTTP / DB (2)
        "http_request", "db_query",
        # Messaging (3)
        "slack_notify", "email_send", "discord_notify",
        # LLM (2)
        "openai_chat", "anthropic_chat",
        # CRM/PM (5)
        "notion_create_page", "notion_query_database",
        "airtable_create_record", "airtable_list_records",
        "linear_create_issue",
        # Dev Tools (2)
        "github_create_issue", "hubspot_create_contact",
    }
    registered = set(registry.list_types())
    missing = expected - registered
    assert not missing, f"ADR-017 node(s) missing from registry: {missing}"
