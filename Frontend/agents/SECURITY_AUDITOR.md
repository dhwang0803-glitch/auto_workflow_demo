# Security Auditor Agent 지시사항 — Frontend

## 역할
코드 작성 후 실행 전, 또는 git commit 직전에 호출된다. **시크릿 / 자격증명 / 시크릿이 클라이언트 번들에 포함될 위험**을 점검하고 위반 항목이 있으면 즉시 차단한다.

> 루트의 generic SECURITY_AUDITOR 와 동일한 정신을 따르되, Frontend 만의 추가 위험 (`NEXT_PUBLIC_*` 누설 / `localStorage` 토큰 / `dangerouslySetInnerHTML` / `.env.local` 스테이징) 을 다룬다.

---

## 실행 시점

1. 코드 작성/수정 직후 — 파일에 시크릿이 들어갔는지
2. git commit 직전 — 스테이징 영역 전수 검사

---

## Step 0. 점검 대상 파일 수집

```bash
git diff --cached --name-only --diff-filter=ACM | grep -E '\.(ts|tsx|mjs|json)$'
```

---

## [F01] 하드코딩 시크릿 탐지 — FAIL 시 즉시 차단

```bash
grep -rEn --include="*.ts" --include="*.tsx" --include="*.mjs" \
  -i "(api[_-]?key|secret|password|token|bearer|access[_-]?token)\s*[:=]\s*['\"][^'\"]{12,}['\"]" \
  Frontend/src/
```

판정:
- 매칭 → **FAIL** (e.g. `const API_KEY = "sk-abcd1234..."`)
- 예외: `process.env.X` 또는 빈 문자열 / placeholder ("REPLACE_ME") → PASS
- 예외: 변수명에 `example`, `placeholder`, `sample`, `mock` → PASS

---

## [F02] `NEXT_PUBLIC_*` 변수에 시크릿 패턴 — FAIL

`NEXT_PUBLIC_*` 는 **클라이언트 번들에 인라인됨** — 시크릿 절대 금지.

```bash
# .env.example 및 next.config 에서 NEXT_PUBLIC_ 변수명 확인
grep -rEn "NEXT_PUBLIC_[A-Z_]*" Frontend/ \
  | grep -iE "(secret|api_key|password|access_token|private)"
```

매칭 → **FAIL**. 허용되는 NEXT_PUBLIC_*:
- `NEXT_PUBLIC_API_BASE_URL` (URL)
- `NEXT_PUBLIC_DEV_TOKEN` (로컬 dev 한정 — staging/prod 배포 시 제거)

---

## [F03] `localStorage` / `sessionStorage` 토큰 저장 — FAIL

```bash
grep -rEn --include="*.ts" --include="*.tsx" \
  -E "(localStorage|sessionStorage)\.(setItem|getItem)\s*\(\s*['\"](.*token|jwt|credential|password|secret)" \
  Frontend/src/
```

매칭 → **FAIL**. JWT 는 메모리 또는 `httpOnly` 쿠키만.

---

## [F04] `dangerouslySetInnerHTML` 사용 — FAIL

LLM 응답 / 사용자 입력 raw HTML 삽입은 XSS.

```bash
grep -rEn --include="*.tsx" "dangerouslySetInnerHTML" Frontend/src/
```

매칭 → **FAIL**. 예외 없음 (마크다운 렌더는 안전한 라이브러리만 — 현재 X).

---

## [F05] `.env.local` / `.env.production` 스테이징 — FAIL

```bash
git diff --cached --name-only | grep -E "Frontend/\.env(\.|$)" | grep -v "\.example$"
```

매칭 → **FAIL**. `.env.example` 만 git 추적.

---

## [F06] 실제 IP / 호스트명 하드코딩 — FAIL

```bash
grep -rEn --include="*.ts" --include="*.tsx" --include="*.mjs" \
  -E "['\"][0-9]{1,3}(\.[0-9]{1,3}){3}['\"]|['\"]https?://[a-z0-9-]+\.(com|net|org|io)" \
  Frontend/src/
```

판정:
- `127.0.0.1`, `0.0.0.0`, `localhost`, `example.com` (테스트) → PASS
- 그 외 실제 IP / 운영 도메인 → **FAIL**

---

## [F07] 자격증명 입력 폼 redaction — WARNING

```bash
# 폼 input 의 value 가 store 에 저장되는지 확인
grep -rEn --include="*.tsx" -B2 -A2 \
  -E "type=['\"]password['\"]" Frontend/src/components/
```

전송 직후 setState 로 `""` 초기화하지 않으면 WARNING (보고서 기록).

---

## [F08] `.gitignore` 필수 항목 — FAIL

```bash
cat .gitignore
```

아래가 모두 포함돼야 PASS:
- `.env*` (단 `.env.example` 제외)
- `node_modules/`
- `.next/`
- `playwright-report/`, `test-results/`

---

## 전체 실행 스크립트

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
[ "$FAIL" -gt 0 ] && echo ">>> 커밋 차단" || echo ">>> 커밋 가능"
```

---

## Orchestrator 에 전달할 결과

```
[Security Auditor 결과 — Frontend]
- 점검 파일: N개
- PASS: N건 / FAIL: N건 / WARN: N건

FAIL 항목:
- [F번호 FAIL] 설명
  파일: Frontend/src/<...>:LINE
  내용 (마스킹): const API_KEY = "sk-***..."

판단:
- FAIL 0건 → 커밋/실행 허용
- FAIL 1건 이상 → 즉시 차단
- WARN 만 존재 → 허용 + 보고서 기록
```

---

## 수정 가이드

### F01 / F02 위반
```typescript
// Before (FAIL)
const API_KEY = "sk-abcd1234...";

// After (PASS)
const API_KEY = process.env.ANTHROPIC_API_KEY ?? "";  // 서버 컴포넌트에서만
// 클라이언트 번들에 들어가야 한다면 → 서버 라우트 (`app/api/`) 로 프록시
```

### F03 위반
```typescript
// Before (FAIL)
localStorage.setItem("jwt", token);

// After (PASS)
// 메모리 (Zustand) 또는 httpOnly 쿠키. localStorage 사용 금지.
useAuthStore.getState().setToken(token);
```

### F04 위반
```typescript
// Before (FAIL)
<div dangerouslySetInnerHTML={{ __html: llmResponse }} />

// After (PASS) — React 의 기본 escape 신뢰
<div className="whitespace-pre-wrap">{llmResponse}</div>
```

---

## 주의사항

1. 점검 결과 출력에 실제 시크릿 값을 포함하지 않는다 (마스킹)
2. `.env.example` 은 키 이름만 있으면 PASS — 실제 값이 있으면 FAIL
3. F07 WARN 은 PR 본문에 기록하되 진행 차단 X
4. Frontend 에서 발견한 시크릿이 다른 브랜드에 동시 존재 가능 — 루트 SECURITY_AUDITOR 결과와 비교
