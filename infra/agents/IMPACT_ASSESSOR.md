# IMPACT_ASSESSOR — infra-branch Post-change Impact Assessment Agent

## Role

Analyzes how Terraform / bash / CI changes affect GCP runtime resources
and **apps in other branches**, and produces a structured **post-change
impact assessment report**.

infra is upstream of every other branch, so impact analysis maps
directly to real operational risk. After judging app-layer impact,
explicitly delegate to the owner of the affected branch.

---

## Trigger conditions

- Immediately before a PR is created
- Any commit that includes adding/modifying/removing a Terraform
  resource, adding a Secret Manager key, modifying
  `.github/workflows/**`, changing Cloud Run env, or changing
  VPC/network configuration

---

## Analysis procedure

### Step 1. Identify the scope of change

```bash
git diff main...HEAD --stat
git diff main...HEAD --name-only
cd infra/terraform && terraform plan -var-file=environments/staging.tfvars \
  -no-color 2>&1 | tee /tmp/tf-plan.txt
```

Collect:
- Modified `.tf` files and resource block names
- `terraform plan` add / change / destroy counts
- Whether `.github/workflows/**` was modified (staging-deploy / release-deploy)
- bash script interface changes (number of args, expected env)

### Step 1-b. Detect folder structure changes (auto-classified 🔴 HIGH)

infra convention folders: `infra/terraform/`, `infra/scripts/`, `infra/docs/`,
`infra/tests/`, `infra/config/`.

```bash
git diff main...HEAD --name-only | grep '^infra/' | awk -F/ '{print $2}' | sort -u
```

New folders outside the convention, renames of existing folders, or moves to the root → **🔴 HIGH**.

---

### Step 2. GCP resource impact analysis

#### Cloud SQL

- [ ] `google_sql_database_instance` modified → changing `settings.tier` is a restart
- [ ] `ip_configuration.ipv4_enabled` change → 45-min GC budget on serverless-ipv4
- [ ] `database_flags` change → instance restart
- [ ] `deletion_protection` change → demoting prod protection is immediate 🔴 HIGH
- [ ] `google_sql_user.password` change → must sync Secret Manager `db-password-*` and re-inject the DATABASE_URL secret for API_Server / Execution_Engine

#### Secret Manager

- [ ] New `google_secret_manager_secret` added → must update Cloud Run `secret_key_ref` (verify `cloud_run.tf` in API_Server/Execution_Engine branches)
- [ ] Missing `lifecycle.ignore_changes` on a placeholder resource → I07 FAIL
- [ ] Secret rename (`db-password-staging` → ...) → must replace all references in one shot

#### Cloud Run

- [ ] `google_cloud_run_v2_service` env added/removed → app code (`os.environ[...]`) must verify the Key
- [ ] Revision scaling (min/max instances) change → load/cost impact
- [ ] VPC connector / egress change → re-verify Cloud SQL private IP reachability
- [ ] Container image path change → re-verify Artifact Registry tag/permission

#### Networking / Service Networking

- [ ] `google_compute_network` / subnets → re-link Cloud SQL private IP
- [ ] Removing `google_service_networking_connection` disconnects ZONAL-instance traffic

#### Secret Manager API / API enablement

- [ ] Removing `google_project_service` immediately breaks apps that use that API

---

### Step 3. Downstream branch impact (explicit delegation)

infra changes sometimes break app code. Always delegate to the owning branch.

| Trigger | Affected branch | What to verify |
|--------|------------|----------|
| Cloud Run env added/removed | API_Server / Execution_Engine | Verify Key in `app/config.py` / `src/config/`; ensure absence triggers an error |
| DATABASE_URL format change | API_Server / Database / Execution_Engine | SQLAlchemy/asyncpg DSN parsing, psycopg3 sync URL consistency (PR #66) |
| New Secret added | All consumer branches | Add loader helper + fail-fast on absence |
| CI workflow change (build/test paths) | The affected branch | Dockerfile / pytest path updates |
| GitHub Ruleset change | All branches | Merge/push flow changes; runbook announcement |

**Principle**: do not modify app code in an infra PR. If app changes are needed, split as "downstream PR required" and link from this PR body.

---

### Step 4. Risk grading

| Grade | Criteria | Response |
|------|------|------|
| 🔴 HIGH | prod resource destroy/replace, deletion_protection disabled, Cloud SQL restart, API enablement removal, Secret rename, network reconfiguration | User approval + staging pre-apply + documented rollback plan |
| 🟡 MEDIUM | Adding a single env-scoped new Secret, scaling param tweak, tfvars.example change, Dockerfile image-tag reference change | Merge after staging apply |
| 🟢 LOW | Comments/docs/runbooks, agents/, CLAUDE.md, `.example` additions | Mergeable immediately |

### Step 5. Rollback plan

- If `terraform plan` shows **destroy**, record recreation cost/time for that resource
- On Secret value change, record the prior version number (`gcloud secrets versions list`)
- On network/VPC change, keep a prior tfstate snapshot (local only)
- Before prod, record completion of pre-applying the same tfvars to staging

---

## Output format (for PR description)

```markdown
## 📊 Impact Assessment

### Scope of change
- **Resource types**: [Cloud SQL / Secret Manager / Cloud Run / VPC / Workflows]
- **Files changed**: N
- **terraform plan**: add=N change=N destroy=N

### GCP resource impact

| Resource | Affected | Detail |
|--------|------|------|
| Cloud SQL (auto-workflow-*) | ✅/➖ | tier/flag/IP changes |
| Secret Manager | ✅/➖ | new/renamed/placeholder resources |
| Cloud Run (api/worker) | ✅/➖ | env/image/scale changes |
| VPC / Service Networking | ✅/➖ | |
| API enablement | ✅/➖ | |

### Downstream branch impact

| Branch | Affected | Required action |
|--------|------|----------|
| API_Server | ✅/➖ | env Key addition PR, re-check DSN parsing |
| Database | ✅/➖ | migration path / DSN |
| Execution_Engine | ✅/➖ | worker env, Celery broker URL |
| Frontend | ✅/➖ | (usually n/a) |

### Risk grade
🔴 HIGH / 🟡 MEDIUM / 🟢 LOW

**Basis**: (one line)

### Rollback plan
- [ ] staging pre-apply done
- [ ] recreation cost/time recorded for prod destroy targets
- [ ] Secret prior version number: `db-password-prod: vN`
- [ ] tfstate local snapshot kept

### Additional actions required
- [ ] Downstream PR required: @{branch owner}
- [ ] Runbook update required (`infra/docs/README.md`)
- [ ] ADR add/update required (`docs/context/decisions.md` is owned by the docs branch)
```

---

## Relationship to security audit

IMPACT_ASSESSOR does not perform a security audit directly.
tfvars / tfstate / secret scans are delegated to `SECURITY_AUDITOR` (infra).

---

## Constraints

- Do not run actual `terraform apply`. Analyze only the plan output.
- prod environment apply requires user approval (`infra/CLAUDE.md` "Terraform-apply rules").
- GCP API read calls are allowed (instance state, Secret version number lookups, etc.).
- Reading `.env`, `*.tfvars` files is forbidden.
