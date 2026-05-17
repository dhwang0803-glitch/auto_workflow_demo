# Security Auditor Agent Instructions — Frontend

## Role
Invoked after code is written and before it runs, or just before a git commit. Checks for **the risk that a secret or credential leaks into the client bundle** and immediately blocks if any violations are found.

> Follows the same spirit as the generic SECURITY_AUDITOR at the repo root, but covers Frontend-specific risks (`NEXT_PUBLIC_*` leakage / `localStorage` token / `dangerouslySetInnerHTML` / `.env.local` staging).

---

## When to run

1. Right after code is written/modified — verify no secret was written into a file
2. Right before `git commit` — scan the entire staged area

---

## Step 0. Collect files to audit

```bash
git diff --cached --name-only --diff-filter=ACM | grep -E '\.(ts|tsx|mjs|json)$'
```

---

## [F01] Hardcoded secret detection — FAIL means immediate block

```bash
grep -rEn --include="*.ts" --include="*.tsx" --include="*.mjs" \
  -i "(api[_-]?key|secret|password|token|bearer|access[_-]?token)\s*[:=]\s*['\"][^'\"]{12,}['\"]" \
  Frontend/src/
```

Decision:
- Match → **FAIL** (e.g., `const API_KEY = "sk-abcd1234..."`)
- Exception: `process.env.X`, empty string, or placeholder ("REPLACE_ME") → PASS
- Exception: variable name contains `example`, `placeholder`, `sample`, `mock` → PASS

---

## [F02] Secret pattern in `NEXT_PUBLIC_*` — FAIL

`NEXT_PUBLIC_*` is **inlined into the client bundle** — secrets are strictly forbidden.

```bash
# inspect NEXT_PUBLIC_ variable names in .env.example and next.config
grep -rEn "NEXT_PUBLIC_[A-Z_]*" Frontend/ \
  | grep -iE "(secret|api_key|password|access_token|private)"
```

Match → **FAIL**. Allowed `NEXT_PUBLIC_*`:
- `NEXT_PUBLIC_API_BASE_URL` (URL)
- `NEXT_PUBLIC_DEV_TOKEN` (local dev only — remove for staging/prod deploy)

---

## [F03] Token stored in `localStorage` / `sessionStorage` — FAIL

```bash
grep -rEn --include="*.ts" --include="*.tsx" \
  -E "(localStorage|sessionStorage)\.(setItem|getItem)\s*\(\s*['\"](.*token|jwt|credential|password|secret)" \
  Frontend/src/
```

Match → **FAIL**. JWT goes in memory or an `httpOnly` cookie only.

---

## [F04] Use of `dangerouslySetInnerHTML` — FAIL

Raw HTML injection from LLM output / user input is an XSS vector.

```bash
grep -rEn --include="*.tsx" "dangerouslySetInnerHTML" Frontend/src/
```

Match → **FAIL**. No exception (markdown rendering only via a safe library — currently not in use).

---

## [F05] `.env.local` / `.env.production` staging — FAIL

```bash
git diff --cached --name-only | grep -E "Frontend/\.env(\.|$)" | grep -v "\.example$"
```

Match → **FAIL**. Only `.env.example` is git-tracked.

---

## [F06] Hardcoded real IP / host name — FAIL

```bash
grep -rEn --include="*.ts" --include="*.tsx" --include="*.mjs" \
  -E "['\"][0-9]{1,3}(\.[0-9]{1,3}){3}['\"]|['\"]https?://[a-z0-9-]+\.(com|net|org|io)" \
  Frontend/src/
```

Decision:
- `127.0.0.1`, `0.0.0.0`, `localhost`, `example.com` (test) → PASS
- Any other real IP / production domain → **FAIL**

---

## [F07] Credential input form redaction — WARNING

```bash
# verify the form input's value is not stored in the store
grep -rEn --include="*.tsx" -B2 -A2 \
  -E "type=['\"]password['\"]" Frontend/src/components/
```

If `setState` does not clear to `""` immediately after submit, WARNING (recorded in the report).

---

## [F08] `.gitignore` required entries — FAIL

```bash
cat .gitignore
```

All of the following must be present to PASS:
- `.env*` (excluding `.env.example`)
- `node_modules/`
- `.next/`
- `playwright-report/`, `test-results/`

---

## Full execution script

```bash
#!/usr/bin/env bash
set -e
echo "=== Frontend Security Audit ==="
FAIL=0
WARN=0

TARGET=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null \
  | grep -E '^Frontend/.*\.(ts|tsx|mjs|json)$' || true)
[ -z "$TARGET" ] && TARGET=$(git diff HEAD --name-only --diff-filter=ACM 2>/dev/null \
  | grep -E '^Frontend/.*\.(ts|tsx|mjs|json)$' || true)

# F01
res=$(echo "$TARGET" | xargs grep -nEi \
  "(api[_-]?key|secret|password|bearer|access[_-]?token)\s*[:=]\s*['\"][^'\"]{12,}['\"]" 2>/dev/null \
  | grep -viE "(process\.env|example|placeholder|sample|mock|REPLACE_ME)") || true
if [ -n "$res" ]; then echo "[F01 FAIL] hardcoded secret"; echo "$res"; FAIL=$((FAIL+1)); else echo "[F01 PASS]"; fi

# F02
res=$(grep -rEn "NEXT_PUBLIC_[A-Z_]*" Frontend/ 2>/dev/null \
  | grep -iE "(secret|api_key|password|access_token|private)") || true
if [ -n "$res" ]; then echo "[F02 FAIL] secret in NEXT_PUBLIC_*"; echo "$res"; FAIL=$((FAIL+1)); else echo "[F02 PASS]"; fi

# F03
res=$(echo "$TARGET" | xargs grep -nE \
  "(localStorage|sessionStorage)\.(setItem|getItem)\s*\(\s*['\"](.*token|jwt|credential|password|secret)" 2>/dev/null) || true
if [ -n "$res" ]; then echo "[F03 FAIL] token in storage"; echo "$res"; FAIL=$((FAIL+1)); else echo "[F03 PASS]"; fi

# F04
res=$(echo "$TARGET" | xargs grep -nE "dangerouslySetInnerHTML" 2>/dev/null) || true
if [ -n "$res" ]; then echo "[F04 FAIL] dangerouslySetInnerHTML"; echo "$res"; FAIL=$((FAIL+1)); else echo "[F04 PASS]"; fi

# F05
res=$(git diff --cached --name-only 2>/dev/null | grep -E "Frontend/\.env(\.|$)" | grep -v "\.example$") || true
if [ -n "$res" ]; then echo "[F05 FAIL] .env staged"; echo "$res"; FAIL=$((FAIL+1)); else echo "[F05 PASS]"; fi

# F06
res=$(echo "$TARGET" | xargs grep -nE \
  "['\"][0-9]{1,3}(\.[0-9]{1,3}){3}['\"]|['\"]https?://[a-z0-9-]+\.(com|net|org|io)" 2>/dev/null \
  | grep -vE "(127\.0\.0\.1|0\.0\.0\.0|localhost|example\.com)") || true
if [ -n "$res" ]; then echo "[F06 FAIL] hardcoded IP/host"; echo "$res"; FAIL=$((FAIL+1)); else echo "[F06 PASS]"; fi

# F08
GI_FAIL=""
grep -q "\.env" .gitignore 2>/dev/null || GI_FAIL="${GI_FAIL} .env"
grep -q "node_modules" .gitignore 2>/dev/null || GI_FAIL="${GI_FAIL} node_modules"
grep -q "\.next" .gitignore 2>/dev/null || GI_FAIL="${GI_FAIL} .next"
if [ -n "$GI_FAIL" ]; then echo "[F08 FAIL] .gitignore missing:${GI_FAIL}"; FAIL=$((FAIL+1)); else echo "[F08 PASS]"; fi

echo "=== FAIL: $FAIL / WARN: $WARN ==="
[ "$FAIL" -gt 0 ] && echo ">>> commit blocked" || echo ">>> commit may proceed"
```

---

## Result to hand to the Orchestrator

```
[Security Auditor result — Frontend]
- Audited files: N
- PASS: N / FAIL: N / WARN: N

FAIL items:
- [F<num> FAIL] description
  File: Frontend/src/<...>:LINE
  Content (masked): const API_KEY = "sk-***..."

Decision:
- 0 FAIL → commit/execution allowed
- 1+ FAIL → immediate block
- Only WARN → allowed + recorded in the report
```

---

## Remediation guide

### F01 / F02 violations
```typescript
// Before (FAIL)
const API_KEY = "sk-abcd1234...";

// After (PASS)
const API_KEY = process.env.ANTHROPIC_API_KEY ?? "";  // server components only
// If it must reach the client bundle → proxy through a server route (`app/api/`)
```

### F03 violation
```typescript
// Before (FAIL)
localStorage.setItem("jwt", token);

// After (PASS)
// Memory (Zustand) or httpOnly cookie. Do not use localStorage.
useAuthStore.getState().setToken(token);
```

### F04 violation
```typescript
// Before (FAIL)
<div dangerouslySetInnerHTML={{ __html: llmResponse }} />

// After (PASS) — trust React's default escaping
<div className="whitespace-pre-wrap">{llmResponse}</div>
```

---

## Cautions

1. Do not include actual secret values in the audit output (mask them)
2. `.env.example` PASSes when it contains key names only — FAILS if it contains actual values
3. F07 WARN is recorded in the PR body but does not block progress
4. A secret found in Frontend may also exist in another brand — compare with the root SECURITY_AUDITOR result
