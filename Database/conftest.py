"""Let `pytest Database/tests` work from either the repo root or `Database/`.

Tests import via `Database.src.repositories...`, so the repo root must be on
sys.path. When pytest is invoked from inside `Database/`, it isn't — this file
fixes that.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
