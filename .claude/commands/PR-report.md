Automatically run the full pipeline from commit to PR creation before opening a PR.

---

## 1. Verify the current branch and changed files

```bash
git branch --show-current
git status
git diff --stat
```

Identify the current branch name and confirm the changed files live inside the **current branch's folder**.
**Never stage files belonging to another brand folder (e.g., API_Server/, Database/, Execution_Engine/, Frontend/).**

---

## 2. Security check (mandatory before commit)

Scan the changed files for the patterns below.

```bash
# detect hardcoded credentials
git diff | grep -E "(password|secret|api_key|token|host)\s*=\s*['\"][^'\"]{4,}"

# detect real-infra info in os.getenv defaults
git diff | grep -E "os\.getenv\(.+,\s*['\"]"
```

| Check | Standard |
|-----------|------|
| Hardcoded credentials | No hardcoded API keys, passwords, or tokens |
| os.getenv() defaults | No real IPs, DB names, or usernames as defaults |
| .env file presence | `.env` is in `.gitignore` |
| data/ presence | `data/` is in `.gitignore` |

- If anything is detected → **stop the commit and request an immediate fix**
- If clean → report "security check passed" and continue

---

## 2-b. Wiki (shared-context docs) update check

**Wiki files (`docs/context/*`) must never be modified or committed from the current code branch.**
Wiki updates happen only on the dedicated `docs` branch and ship as a separate PR.

### Inspection order (must follow this order)

#### Step 1 — Decision Audit (**mandatory precursor**)

> ⚠️ **Caution**: It is forbidden to report "no wiki update needed" based only on "the diff does not contain `docs/context/`."
> That checks "did I touch it," not "is it up to date." A PR that skips this audit
> and only runs Step 2's diff check is a **rule violation**.

Enumerate every **decision** this PR contains. A "decision" means a **judgment** that drove a
code / doc / config change — a simple refactor or bug fix is not a decision.

Decision candidates (self-audit checklist):
- Does this PR add a new technology / library / extension / image / tool?
- Does this PR settle a **contract shape** (JSON key, DTO field, API signature, set of status values)?
- Does this PR change a **data flow** shared by two or more branches (who writes, who reads)?
- Does this PR change a security policy (storage / transit / logging)?
- Does this PR change or introduce an operations procedure (scheduler, backup, migration, partitioning)?
- Does this PR shift the MVP / Phase boundary (pull something into the MVP or push something to Phase 2)?

For each decision, answer two questions:

1. **Without knowing this decision, could another branch's worker proceed on a wrong premise?**
2. **Can the current wiki (`architecture.md` / `decisions.md` / `MAP.md`) alone communicate this decision?**

If Q1 is **Yes** and Q2 is **No** → **wiki update needed**.

#### Step 2 — Trigger mapping

Map decisions classified as "update needed" in Step 1 to the table below.

| Change kind | Update target (on the docs branch) |
|-----------|-----------|
| New top-level folder/branch added; file placement rule changed | `docs/context/MAP.md` |
| 4-layer flow / data path / new execution mode / write order between Repositories | `docs/context/architecture.md` |
| Tech-stack swap or addition, security-policy change, design decision with trade-offs, **contract shape another branch will depend on** (JSON key, status value, API signature) | `docs/context/decisions.md` (add a new ADR or append an `**Update (YYYY-MM-DD)**` section to an existing ADR. For Superseded, mark the existing entry) |

When **refining/strengthening** an existing ADR (not Superseded), prefer adding to that ADR's
`Update` section over creating a new ADR. Create a new ADR only when the new decision is
"independent from the existing one".

Changes to internal branch structure / convention (`_claude_templates/CLAUDE_*.md`) may be made in the code branch alongside the change — it is that branch's concern.

#### Step 3 — Diff-mistake guard

If the current branch's diff contains a `docs/context/` file **by accident** → **stash or restore it and stop**; tell the user to move to the `docs` branch and create a separate PR.
(Steps 1/2 find "what should be done"; Step 3 catches "what was done by mistake.")

### Reporting format

The audit result must be reported explicitly in one of the forms below. A one-line "wiki update not needed" report is **forbidden** — always include the audit detail.

- **Case A — no update needed**:
  ```
  [Wiki audit]
  N decisions: <decision 1>, <decision 2>, ...
  Q1/Q2 verdict for each decision: <all branch-local / covered by existing wiki>
  → no update needed
  ```
- **Case B — update needed but not yet done**:
  ```
  [Wiki audit]
  Update-needed decision: <decision name>
  → must open a `docs` branch PR before/after this code PR
  → add "Wiki update PR required: <description>" to the "Impact Assessment" section of the code PR body
  ```
- **Case C — update done**: if a `docs` branch PR has already been opened in this PR cycle, link / reference the PR number in this PR body.

---

## 3. Stage and commit current-branch files only

Run only when there are uncommitted changes.

```bash
git add {current branch folder}/
git commit -m "..."
```

**Files that must not be committed**: `.env`, `data/`, `*.parquet`, `*.pkl`, `*.pem`, `credentials.json`

---

## 4. Refresh the base branch

```bash
# 1) fetch latest from remote
git fetch origin

# 2) check divergence between base (main) and current branch
git log HEAD..origin/main --oneline
git log origin/main..HEAD --oneline
```

- If `origin/main` has commits the local branch lacks → **run `git pull` first**
- On conflict → list the conflicting files to the user and **stop**. Ask them to resolve and re-invoke.
- If there is no divergence → continue

```bash
# run only when there is divergence
git pull origin main
```

---

## 5. Analyze the changes

```bash
# list of files changed vs the base
git diff --name-status origin/main...HEAD

# commit history
git log origin/main..HEAD --oneline
```

- Number and list of files changed (added/modified/deleted)
- Summary of each commit's main content

---

## 6. Inspect previous PR contents

```bash
gh pr list --head {current branch} --state all --limit 1
gh pr view {PR number} --json body
```

- If a previous PR exists, read its body to understand it.
- When writing the new PR body, **overwrite duplicate items with the latest content**, and **add new items to the matching section**.
- If no previous PR exists, write a fresh one.

---

## 7. Create the PR

Based on the analysis above, write the PR body in the format below and run `gh pr create`.
The PR base branch is always `main`.

```
## Change summary
<!-- For each changed file, describe what changed and why (max 3 bullets) -->

## Impact assessment
| Impact area | Detail | Action needed |
|-----------|------|---------------|
| Upstream dependency | ... | Yes / No |
| Downstream dependency | ... | Yes / No |
| DB schema change | ... | Yes / No |
| API interface change | ... | Yes / No |

## Security assessment
| Check | Result |
|-----------|------|
| Hardcoded credentials | ✅/❌ |
| os.getenv() default exposes infra | ✅/❌ |
| `.env`, `data/` in gitignore | ✅/❌ |
| External input validation | ✅/❌ |

## Test checklist
- [ ] Local run verified
- [ ] Unit tests on key changed functions
- [ ] Review requested from the relevant team member

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

---

## ⛔ Absolute rules (apply to every execution subject, Claude included)

**The following actions must never be executed without explicit user approval.**

1. `git push origin main` — no direct push to main
2. No direct merge to main without a PR
3. No merge without PR review (Approve)
4. Do not include files from another brand folder in the current branch's commit
5. During PR creation, do not additionally commit/push files that were not requested

**These rules hold until the user explicitly says "push it" or "merge it."**

> Violation: stop immediately and report to the user.
