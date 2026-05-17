# IMPACT_ASSESSOR — Post-change Impact Assessment Agent

## Role

Before a PR is opened, analyzes how the change affects every layer of the
project and produces a structured **post-change impact assessment report**.

---

## Trigger conditions

- Immediately before a PR is created (point at which code changes are complete)
- Any commit that includes a schema / API / node-interface change

---

## Analysis procedure

### Step 1. Identify the scope of change

```bash
git diff main...HEAD --stat
git diff main...HEAD --name-only
```

Check:
- The list of changed files and their layer classification (Database / API_Server / Execution_Engine / Frontend)
- Lines added / deleted / modified
- Newly created files vs. modifications to existing files

### Step 1-b. Detect folder structure changes (auto-classified 🔴 HIGH)

```bash
git diff main...HEAD --name-only | grep -E "^[^/]+/[^/]+/" | \
  awk -F/ '{print $1"/"$2}' | sort -u
```

If any of the patterns below is detected, **immediately classify as 🔴 HIGH**.

| Pattern detected | Decision | Reason |
|-----------|------|------|
| New top-level folder outside the convention (e.g., `data/`, `notebooks/`, `utils/`) | 🔴 HIGH | Folder structure rule violation |
| An existing folder moved under another folder | 🔴 HIGH | Violation of team-wide agreement |
| Convention folder renamed (e.g., `scripts/` → `script/`) | 🔴 HIGH | Folder structure rule violation |

**Convention folders per branch**:
- `API_Server/`: `app/routers/`, `app/services/`, `app/models/`, `tests/`, `config/`
- `Database/`: `schemas/`, `migrations/`, `src/repositories/`, `src/models/`, `scripts/`, `tests/`, `docs/`
- `Execution_Engine/`: `src/nodes/`, `src/dispatcher/`, `src/runtime/`, `src/agent/`, `scripts/`, `tests/`, `config/`, `docs/`
- `Frontend/`: `src/components/`, `src/pages/`, `src/services/`, `public/`, `tests/`

---

### Step 2. Per-layer impact analysis

#### Database layer

- [ ] DDL changes (ALTER TABLE / CREATE / DROP)
- [ ] Existing column type change → risk of data loss
- [ ] Adding NOT NULL constraint → verify existing NULL rows
- [ ] Index change → query performance impact
- [ ] Repository interface (ABC) change → downstream impact on API_Server / Execution_Engine
- [ ] Presence of a migration script (`migrations/`)

#### API_Server layer

- [ ] Endpoint added/removed/path changed
- [ ] Request/response Pydantic schema change
- [ ] Agent communication protocol (AgentCommand/AgentStatus) change → verify Agent backward compatibility
- [ ] Webhook path / auth method change
- [ ] DAG scheduler / Trigger logic change

#### Execution_Engine layer

- [ ] `BaseNode` interface change → every node must be re-implemented
- [ ] New node added → check that `NodeRegistry.register()` is not missing
- [ ] Sandbox constraint change → impact on existing CodeExecutionNode
- [ ] Celery task signature change → queue backlog compatibility
- [ ] Agent protocol message change → breaks previously installed Agents

#### Frontend layer

- [ ] Change in API endpoint call signatures
- [ ] Node parameter schema change → update NodeConfigPanel
- [ ] Credential input form follows security rules

### Step 3. Risk grading

| Grade | Criteria | Response |
|------|------|------|
| 🔴 HIGH | Existing data loss / downstream breakage / breaks compatibility with deployed Agents | Full team review required |
| 🟡 MEDIUM | Single-layer interface change / performance impact | Owner review before merge |
| 🟢 LOW | Additions only / internal logic improvement / doc edits | Auto-merge allowed |

### Step 4. Rollback plan

- If a migration exists, presence of a DOWN script
- Backward compatibility for deployed Agents
- Need for a DB snapshot before deploy

---

## Output format (for PR description)

```markdown
## 📊 Impact Assessment

### Scope of change
- **Layer**: [Database / API_Server / Execution_Engine / Frontend / docs]
- **Files changed**: N
- **Change kind**: [new addition / modification / deletion / refactor]

### Per-layer impact

| Layer | Affected | Detail |
|--------|-----------|------|
| Folder structure rule | ✅ Compliant / 🔴 Violation | |
| Database schema | ✅ Affected / ➖ Not applicable | |
| API contract | ✅ Affected / ➖ Not applicable | |
| Execution_Engine (nodes/sandbox) | ✅ Affected / ➖ Not applicable | |
| Agent protocol | ✅ Affected / ➖ Not applicable | |
| Frontend | ✅ Affected / ➖ Not applicable | |

### Risk grade
🔴 HIGH / 🟡 MEDIUM / 🟢 LOW

**Basis**: (one-line rationale)

### Rollback plan
- [ ] Migration DOWN script prepared
- [ ] Previous version tag exists: `git tag vX.Y.Z`
- [ ] Agent backward compatibility verified

### Additional actions required
- [ ] None
- [ ] Downstream-branch owner review: @{owner}
- [ ] Notice for deployed Agents to force-update
```

---

## Relationship to security audit

IMPACT_ASSESSOR does **not** perform a security audit directly.
The security audit is owned by the `SECURITY_AUDITOR` agent.

---

## Constraints

- Analysis scope: based on `git diff main...HEAD`
- If actual DB state is needed, only read-only queries are allowed
- Reading `.env` files is forbidden
- The impact analysis is **inference-based**; real deployment impact must be verified in staging
