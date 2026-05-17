# Security Auditor Agent Instructions

## Role
Invoked after code is written and before it runs, or just before a git commit.
Checks whether **personally identifiable information, credentials, or real
infrastructure details** have leaked into source files or the staging area,
and immediately blocks if any violations are found.

Applies to **all branches** including API_Server, Database, Execution_Engine,
and Frontend.

---

## When to run

1. **Right after code is written/modified, before it runs** — verify no credentials were committed to a file
2. **Right before `git commit`** — scan the entire staged area and decide whether the commit is allowed

---

## Audit procedure

### Step 0. Collect files to audit

```bash
# Option A: staged files (immediately before commit)
git diff --cached --name-only --diff-filter=ACM

# Option B: recently modified files (pre-execution check)
git diff HEAD --name-only --diff-filter=ACM
# if empty, fall back to the last commit
git diff HEAD~1 HEAD --name-only --diff-filter=ACM
```

Run the checks below against the collected file list.

---

### [S01] Hardcoded credential detection — FAIL means immediate block

Target: all `.py` files in the collection

```bash
grep -rn --include="*.py" \
  -iE "(api_key|password|secret|token|passwd|pwd)\s*=\s*['\"][^'\"]{6,}['\"]" \
  <target files>
```

**Decision criteria**:
- Any matching line → **FAIL**
- Exception: `os.getenv(...)`, `dotenv_values(...)`, `config.get(...)` forms PASS
- Exception: variable names containing `example`, `sample`, `test`, or `placeholder` PASS

---

### [S02] os.getenv() real-infra default detection — FAIL means immediate block

```bash
grep -rn --include="*.py" \
  -E "os\.getenv\s*\([^)]+,\s*['\"][^'\"]+['\"]" \
  <target files>
```

In the extracted lines, the default value (second argument) **FAILS** if it matches any of:
- Real IP pattern: `\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b`
- Database name pattern: a project-specific DB name other than `localhost` or `postgres` (e.g., `myapp_db`, `prod_db`)
- Username pattern: a project-specific user other than `postgres` (e.g., `admin`, `dbadmin`)

Allowed default values (PASS):
- `"localhost"`, `"5432"`, `"postgres"`, `""`, `"http://localhost:11434"`, `"0.0.0.0"`

---

### [S03] env.get() / dict.get() real-infra default detection — FAIL means block

```bash
grep -rn --include="*.py" \
  -E "env\.get\s*\([^)]+,\s*['\"][^'\"]+['\"]" \
  <target files>
```

Same decision criteria as S02.

---

### [S04] Hardcoded real IP address detection — FAIL means block

```bash
grep -rn --include="*.py" \
  -E "\"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\"" \
  <target files>
```

**Decision criteria**:
- `"127.0.0.1"`, `"0.0.0.0"` → PASS (loopback / wildcard)
- Any other real IP → **FAIL**

---

### [S05] `.env` file staging check — FAIL means immediate block

```bash
git diff --cached --name-only | grep -E "(^|/)\.env(\.|$)"
```

`.env`, `.env.local`, `.env.production`, etc. staged → **FAIL**
`.env.example` → PASS

---

### [S06] Sensitive file tracked by git — FAIL means block

```bash
git ls-files | grep -E "\.(env|pem|key|p12|pfx)$|credentials\.json|api_keys\.env|secrets\.json"
```

Any of the above patterns tracked by git → **FAIL**

---

### [S07] `.gitignore` missing required entry — FAIL means block

```bash
cat .gitignore
```

All of the following entries must be present to PASS:
- `.env` or `.env.*`
- `*.pem`
- `*.key`
- `credentials.json`
- `.claude/settings.local.json`

Any one missing → **FAIL**

---

### [S08] Hardcoded local path — WARNING (commit allowed, must be reported)

```bash
grep -rn --include="*.py" \
  -E "\"C:/Users/[^\"]+\"|'C:/Users/[^']+'" \
  <target files>
```

**Decision criteria**:
- A module-top-level constant (`DEFAULT_*`, `MODEL_PATH`, etc.) that can be overridden by a CLI argument (`argparse`) → **WARNING** (allowed)
- Used directly inside a function → **FAIL**

How to decide: confirm whether the matching line is inside a function

```bash
# inspect context around the line (-B5: 5 lines above)
grep -n "C:/Users/" <file> | while read line; do
  lineno=$(echo "$line" | cut -d: -f1)
  # if the 5 lines above lineno contain `def `, it is inside a function
done
```

---

## Full execution script

Run the script below with the Bash tool. Replace `TARGET_FILES` with the file list collected in Step 0.

```bash
#!/usr/bin/env bash
# run from the project root (git repo root)

echo "=== Security Audit start ==="
echo "Audit time: $(date '+%Y-%m-%d %H:%M')"
FAIL_COUNT=0
WARN_COUNT=0

# Step 0: collect files to audit
STAGED=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null)
MODIFIED=$(git diff HEAD --name-only --diff-filter=ACM 2>/dev/null)
TARGET_PY=$(echo -e "${STAGED}\n${MODIFIED}" | grep '\.py$' | sort -u)

if [ -z "$TARGET_PY" ]; then
  TARGET_PY=$(git diff HEAD~1 HEAD --name-only --diff-filter=ACM 2>/dev/null | grep '\.py$')
fi

echo "Audited files: $(echo "$TARGET_PY" | grep -c '.py')"
echo "---"

# S01: hardcoded credentials
result=$(echo "$TARGET_PY" | xargs grep -n \
  -iE "(api_key|password|secret|token|passwd|pwd)\s*=\s*['\"][^'\"]{6,}['\"]" 2>/dev/null \
  | grep -viE "(os\.getenv|dotenv|config\.get|example|sample|test|placeholder)")
if [ -n "$result" ]; then
  echo "[S01 FAIL] hardcoded credential detected"
  echo "$result"
  FAIL_COUNT=$((FAIL_COUNT + 1))
else
  echo "[S01 PASS] hardcoded credentials"
fi

# S02: os.getenv() real-infra default
result=$(echo "$TARGET_PY" | xargs grep -n \
  -E "os\.getenv\s*\([^)]+,\s*['\"][^'\"]+['\"]" 2>/dev/null \
  | grep -vE "(localhost|5432|postgres|http://localhost|0\.0\.0\.0|\"\")")
if [ -n "$result" ]; then
  echo "[S02 FAIL] os.getenv() real-infra default"
  echo "$result"
  FAIL_COUNT=$((FAIL_COUNT + 1))
else
  echo "[S02 PASS] os.getenv() default"
fi

# S03: env.get() real-infra default
result=$(echo "$TARGET_PY" | xargs grep -n \
  -E "env\.get\s*\([^)]+,\s*['\"][^'\"]+['\"]" 2>/dev/null \
  | grep -vE "(localhost|5432|postgres|http://localhost|0\.0\.0\.0|\"\")")
if [ -n "$result" ]; then
  echo "[S03 FAIL] env.get() real-infra default"
  echo "$result"
  FAIL_COUNT=$((FAIL_COUNT + 1))
else
  echo "[S03 PASS] env.get() default"
fi

# S04: hardcoded real IP
result=$(echo "$TARGET_PY" | xargs grep -n \
  -E "\"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\"" 2>/dev/null \
  | grep -vE "(127\.0\.0\.1|0\.0\.0\.0)")
if [ -n "$result" ]; then
  echo "[S04 FAIL] hardcoded real IP"
  echo "$result"
  FAIL_COUNT=$((FAIL_COUNT + 1))
else
  echo "[S04 PASS] IP hardcoding"
fi

# S05: .env file staged
result=$(git diff --cached --name-only 2>/dev/null | grep -E "(^|/)\.env(\.|$)" | grep -v "\.example")
if [ -n "$result" ]; then
  echo "[S05 FAIL] .env file is staged"
  echo "$result"
  FAIL_COUNT=$((FAIL_COUNT + 1))
else
  echo "[S05 PASS] .env staging"
fi

# S06: sensitive file tracked by git
result=$(git ls-files 2>/dev/null | grep -E "\.(env|pem|key|p12|pfx)$|credentials\.json|api_keys\.env$|secrets\.json" | grep -v "\.example")
if [ -n "$result" ]; then
  echo "[S06 FAIL] sensitive file tracked by git"
  echo "$result"
  FAIL_COUNT=$((FAIL_COUNT + 1))
else
  echo "[S06 PASS] sensitive file tracking"
fi

# S07: .gitignore required entries
GITIGNORE_FAIL=""
grep -q "\.env" .gitignore 2>/dev/null || GITIGNORE_FAIL="${GITIGNORE_FAIL} .env"
grep -q "\*\.pem" .gitignore 2>/dev/null || GITIGNORE_FAIL="${GITIGNORE_FAIL} *.pem"
grep -q "\*\.key" .gitignore 2>/dev/null || GITIGNORE_FAIL="${GITIGNORE_FAIL} *.key"
grep -q "credentials\.json" .gitignore 2>/dev/null || GITIGNORE_FAIL="${GITIGNORE_FAIL} credentials.json"
grep -q "settings\.local\.json" .gitignore 2>/dev/null || GITIGNORE_FAIL="${GITIGNORE_FAIL} settings.local.json"
if [ -n "$GITIGNORE_FAIL" ]; then
  echo "[S07 FAIL] .gitignore missing entries:${GITIGNORE_FAIL}"
  FAIL_COUNT=$((FAIL_COUNT + 1))
else
  echo "[S07 PASS] .gitignore required entries"
fi

# S08: hardcoded local path (WARNING)
result=$(echo "$TARGET_PY" | xargs grep -n \
  -E "\"C:/Users/[^\"]+\"|'C:/Users/[^']+'" 2>/dev/null)
if [ -n "$result" ]; then
  echo "[S08 WARN] hardcoded local path — verify constant + CLI override"
  echo "$result"
  WARN_COUNT=$((WARN_COUNT + 1))
else
  echo "[S08 PASS] hardcoded local paths"
fi

echo ""
echo "=== Security Audit complete ==="
echo "FAIL: ${FAIL_COUNT} / WARN: ${WARN_COUNT}"
if [ "$FAIL_COUNT" -gt 0 ]; then
  echo ">>> commit blocked — fix FAIL items and re-run"
else
  echo ">>> commit may proceed"
fi
```

---

## Result format to hand to the Orchestrator

```
[Security Auditor result]
- When run: after code edit / before commit
- Audited files: N
- PASS: N / FAIL: N / WARN: N

FAIL items:
- [S<num> FAIL] description
  Offending file: path/to/file.py:<line>
  Offending content: (actual value redacted — e.g., api_key = "ab**...")

Decision:
- 0 FAIL → commit/execution allowed
- 1+ FAIL → immediate block, request a fix
- Only WARN → allowed, recorded in the report
```

---

## Remediation guide

### Fix for S01/S02/S03 violations
```python
# Before (FAIL)
DB_HOST = "10.0.0.1"
api_key = "abcd1234efgh"
host = os.getenv("DB_HOST", "10.0.0.1")

# After (PASS)
DB_HOST = os.getenv("DB_HOST")
api_key = os.getenv("TMDB_API_KEY", "")
host = os.getenv("DB_HOST")
```

### Fix for S05 violations
```bash
git rm --cached .env
echo ".env" >> .gitignore
```

### S08 WARNING — verify allowed conditions
```python
# WARNING allowed (module-top constant + CLI override exists)
DEFAULT_TRAILERS_DIR = Path("C:/Users/daewo/DX_prod_2nd/trailers")  # allowed
parser.add_argument('--trailers-dir', default=str(DEFAULT_TRAILERS_DIR))

# Promoted to FAIL (used directly inside a function)
def process():
    path = Path("C:/Users/daewo/DX_prod_2nd/trailers")  # FAIL
```

---

## Cautions

1. Do not include actual credential values in the audit output (mask them)
2. S08 WARN entries are recorded under "Security Notes" in the report but do not block progress
3. S05/S06 are only effective between `git add` and `git commit`
4. `.env.example` PASSes when it contains only key names with no sensitive values
