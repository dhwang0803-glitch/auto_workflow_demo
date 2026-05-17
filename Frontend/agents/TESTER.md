# Tester Agent Instructions — Frontend

## Role
After Developer Agent finishes the implementation, runs TypeScript type-check + ESLint + Next.js production build + Playwright tests and collects the results.

---

## Runtime environment

- Node.js 20+ (LTS)
- pnpm or npm (lockfile is `pnpm-lock.yaml`)
- Windows 11 (PowerShell or Git Bash)
- Playwright browsers (`npx playwright install` once)

---

## Process management rules (MANDATORY)

1. **Kill the previous process before re-running** — if a Playwright dev server lingers as a zombie, ports collide
   ```bash
   # Git Bash / PowerShell
   taskkill //F //IM node.exe 2>/dev/null
   ```
2. No background execution — read results immediately in the foreground
3. `playwright test --reuse-existing-server` is the default (`playwright.config.ts`) — reuses an already-running dev server

---

## Verification order (mandatory before opening a PR, all green)

```bash
# 1. type check (5 s)
npx tsc --noEmit

# 2. lint (5 s) — react/no-unescaped-entities trips frequently
npx next lint

# 3. production build (~30 s) — for route-size verification
npx next build

# 4. Playwright (mock-based, ~15 s)
npx playwright test tests/ai-composer.spec.ts tests/skill-wizard.spec.ts
```

Fix `tsc` / `lint` failures immediately. `build` failures usually come from Server/Client component boundary issues (e.g., missing `"use client"`).

---

## Live integration tests (optional)

`smoke.spec.ts` requires API_Server uvicorn on :8000 + Postgres + a dev token:

```bash
# bring up API_Server (separate terminal)
cd ../API_Server
uvicorn app.main:app --reload --port 8000

# bring up Frontend dev and run smoke
cd ../Frontend
npx playwright test tests/smoke.spec.ts
```

Run only in integration-verification stages like W2-8a. Skip in regular PRs — environment dependency is a burden for PR CI.

---

## Result format

```
[Tester run results]
- Run environment: Node {version}, Playwright {version}
- type check: PASS / FAIL (N errors)
- lint: PASS / FAIL (N warnings / N errors)
- build: PASS / FAIL (route-size delta)
- Playwright (mock): X/Y passed
- Playwright (live, smoke): X/Y or SKIP (reason)

FAIL items:
- [tests/<file>.spec.ts:LINE] [error message summary]
- [src/<file>:LINE] [tsc error code + message]

Next action:
- All PASS → proceed to commit
- FAIL exists → kill node.exe → fix the cause → re-run
```

---

## Cautions

1. A stale dev server can make lint/build failures look false — if suspected, delete `.next/` and re-run
2. Do not attempt to install a new dependency without changing `pnpm-lock.yaml` — the lockfile is the source of truth in Frontend
3. Playwright's `webServer` startup emits `Cross origin request detected from 127.0.0.1` warnings — harmless, ignore
4. Live integration failure (`ECONNREFUSED 127.0.0.1:8000`) means API_Server is not running — not a Frontend regression
5. Record the route sizes from `npx next build` in the report (track the trend)
