# {BRANCH} — Claude Code branch guide

> Applied alongside the root `CLAUDE.md` security rules.
> **This file is the default template. Customize it for the branch's role.**

## Module role

TODO: describe in one sentence the functionality this branch owns.

## File layout rules (MANDATORY)

```
{BRANCH}/
├── src/       ← import-only library (not directly executed)
├── scripts/   ← directly executed scripts (python scripts/xxx.py)
├── tests/     ← pytest
├── config/    ← yaml, .env.example
└── docs/      ← design docs, reports
```

| File kind | Storage location |
|-----------|------------------|
| Modules / utility functions to be imported | `src/` |
| Executed as `python scripts/run_xxx.py` | `scripts/` |
| pytest | `tests/` |
| `.yaml`, `.env.example` | `config/` |
| Docs, reports | `docs/` |

**Do not create `.py` files directly under `{BRANCH}/` or the project root.**

## Tech stack

TODO: list the main libraries.

```python
import psycopg2
from dotenv import load_dotenv
```

## Import rules

```python
# How to import src/ modules from scripts/
ROOT = Path(__file__).resolve().parents[2]  # parents[2] from scripts/ is ROOT
_SRC = ROOT / "{BRANCH}" / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
import my_module
```

## Interfaces

- **Upstream**: TODO (what data / result is consumed)
- **Downstream**: TODO (what data / result is produced)
