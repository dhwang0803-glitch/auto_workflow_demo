# PLAN_00 — Split Database into the `auto-workflow-database` Python package

> **Branch**: `Database` · **Drafted**: 2026-04-15 · **Completed**: 2026-04-15 · **Status**: Done
>
> Ahead of API_Server / Execution_Engine kickoff, convert Database from
> sys.path-dependent monorepo imports (`Database.src.*`) to a proper
> Python package. Zero behavioral change; the structure changes entirely.

## 1. Background

Until now other branches referenced Database as
`from Database.src.repositories.base import ...`, which had:

- A structural fragility: imports only resolve when the repo root is on sys.path
- Forced `git pull origin main` on other branches every time they wanted Database's latest code
- An unclear package boundary (internal helpers could be imported from outside)

API_Server is about to depend directly on the repositories, so this has to be
fixed now.

## 2. Decision

**Phase 1 (this PLAN)**: `pyproject.toml`-based editable local install

- Add `Database/pyproject.toml` (`auto-workflow-database`, v0.1.0)
- Physically move `Database/src/` → `Database/auto_workflow_database/` (`git mv`)
- Bulk-rewrite import paths in 19 files (`Database.src.` → `auto_workflow_database.`)
- Other branches install with `pip install -e Database/`

**Phase 2 (follow-up, timing TBD)**: publish a wheel on GitHub Packages

- Each Database release builds a wheel in CI and pushes it to GitHub Packages
- Other branches version-pin (`auto-workflow-database==0.2.1`)
- API_Server's `import` statements **do not change one line on the Phase 1 → 2 swap**
  (only the install source changes from editable to published)

## 3. Scope

**In**
- Write `pyproject.toml` (setuptools build backend, dependency declarations)
- Rename the directory (`src/` → `auto_workflow_database/`) via `git mv`
- Bulk-rewrite import paths (19 files)
- Remove the sys.path hack from `conftest.py`
- Update the file-location rules in `CLAUDE.md`
- Verify the whole test suite is regression-free (24/24)

**Out**
- The Phase 2 CI / publishing pipeline
- Actually installing the package on the API_Server / Execution_Engine branches (each branch's own PLAN)
- New features / schemas / endpoints

## 4. Deliverables

| Path | Content |
|------|---------|
| `pyproject.toml` | Package metadata + dependency declarations |
| `auto_workflow_database/` | Moved-in contents of the old `src/` |
| `conftest.py` | sys.path hack removed, kept only for markers |
| `CLAUDE.md` | Directory rules + import-path table updated |
| `plans/PLAN_00_PACKAGE_REFACTOR.md` | This document |

## 5. Acceptance criteria

- [x] `pip install -e Database/` succeeds cleanly
- [x] `python -c "from auto_workflow_database.repositories.base import CredentialStore"` works
- [x] Full test suite 24/24 passes (including DB tests) *(2026-04-15)*
- [x] Zero references to `Database.src` remain (`git grep`)
- [x] The directory-rules block in `CLAUDE.md` reflects the new structure

## 6. Downstream impact

- **API_Server PLAN_01** — add `auto-workflow-database` to dependencies and
  install locally via `pip install -e ../Database`. Reference the ABCs as
  `from auto_workflow_database...`
- **Execution_Engine** — same arrangement
- **On Phase 2 transition** — add wheel-publishing config to `pyproject.toml`
  plus a GH Actions workflow. Other branches only swap the dependency line
  from `file://` to a version pin.
