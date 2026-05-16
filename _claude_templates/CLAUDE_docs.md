# docs — Claude Code branch guide (wiki edits only)

> This branch is forbidden from changing code. Edit only wiki documents under `docs/context/`.

## Role

The branch that manages the project's **shared knowledge base (wiki)**.
ADRs, architecture diagrams, file maps, and design-decision background —
documents referenced in common across multiple branches — are updated only here.

Code branches (`API_Server` / `Database` / `Execution_Engine` / `Frontend`)
reference the wiki **read-only**; when an update is needed, open a separate PR on this branch.

## Related documents

- Full architecture: [`docs/context/architecture.md`](../docs/context/architecture.md)
- Decision rationale: [`docs/context/decisions.md`](../docs/context/decisions.md)
- File map: [`docs/context/MAP.md`](../docs/context/MAP.md)

## Edit scope (MANDATORY)

Allowed:
- `docs/context/*.md` — shared knowledge such as architecture / ADR / MAP
- `README.md` — top-level project description
- "Related documents" section of `_claude_templates/*.md` — cross-reference link upkeep

Forbidden:
- All source code such as `.py`, `.ts`, `.tsx`, `.sql`
- Any file under `API_Server/`, `Database/`, `Execution_Engine/`, `Frontend/`
- Behavior changes to `.githooks/`, `.github/` (doc-only edits — not functional changes — are exceptions)

## Document update principles

1. **architecture.md**: Edit only when the 4-layer flow / paths change. Per-branch internals belong in each branch's `CLAUDE.md`.
2. **decisions.md**: When overturning an existing decision, mark the prior ADR with *Superseded by ADR-###* and add a new ADR. Never delete.
3. **MAP.md**: Update only when a new top-level folder/branch appears. Do not update every time files grow (it is a top-level structure map, not a file index).
4. If wiki changes are mixed into a code-branch PR, request a split.

## PR flow

```
1) check out the docs branch
   git checkout docs && git pull origin main
2) edit docs/context/
3) run /PR-report → open a PR (base: main)
4) after merge, code branches absorb it naturally on the next main pull
```

## Notes

- When the wiki diverges from the code, **the code wins**. Fix the wiki to match the code.
- Different role from memory (`~/.claude/.../memory/`): the wiki is **shared team knowledge (git-tracked)**;
  memory is **Claude's per-session personal knowledge (local files)**. Do not copy between them.
