"""Pytest bootstrap for the Database package.

After PLAN_00 the package is installed editable via `pip install -e Database/`
from the repo root, so Python resolves `auto_workflow_database` normally on
sys.path. No shim required — this file stays as a marker so pytest roots the
test session at Database/.
"""
