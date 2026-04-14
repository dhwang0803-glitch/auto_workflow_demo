# Security Auditor Agent 지시사항

## 역할
코드 작성 후 실행 전, 또는 git commit 직전에 호출된다.
**개인식별 정보·자격증명·실제 인프라 정보**가 코드나 스테이징 영역에 노출되었는지 점검하고,
위반 항목이 있으면 즉시 차단한다.

API_Server, Database, Execution_Engine, Frontend 등 **모든 브랜치에 적용**한다.

---

## 실행 시점

1. **코드 작성/수정 직후, 실행 전** — 파일에 자격증명이 들어갔는지 확인
2. **git commit 직전** — 스테이징 영역 전수 검사 후 커밋 허용 여부 결정

---

## 점검 절차

### Step 0. 점검 대상 파일 수집

```bash
# 방법 A: 스테이징된 파일 (커밋 직전)
git diff --cached --name-only --diff-filter=ACM

# 방법 B: 최근 수정된 파일 (실행 전 점검)
git diff HEAD --name-only --diff-filter=ACM
# 없으면 마지막 커밋 기준
git diff HEAD~1 HEAD --name-only --diff-filter=ACM
```

수집한 파일 목록을 기준으로 이하 체크를 실행한다.

---

### [S01] 하드코딩 자격증명 탐지 — FAIL 시 즉시 차단

점검 대상: 수집된 `.py` 파일 전체

```bash
grep -rn --include="*.py" \
  -iE "(api_key|password|secret|token|passwd|pwd)\s*=\s*['\"][^'\"]{6,}['\"]" \
  <대상 파일들>
```

**판정 기준**:
- 매칭 라인이 있으면 → **FAIL**
- 예외: `os.getenv(...)`, `dotenv_values(...)`, `config.get(...)` 형태는 PASS
- 예외: 변수명에 `example`, `sample`, `test`, `placeholder` 포함 시 PASS

---

### [S02] os.getenv() 실제 인프라 기본값 탐지 — FAIL 시 즉시 차단

```bash
grep -rn --include="*.py" \
  -E "os\.getenv\s*\([^)]+,\s*['\"][^'\"]+['\"]" \
  <대상 파일들>
```

추출된 라인에서 기본값(두 번째 인자)이 아래에 해당하면 **FAIL**:
- 실제 IP 패턴: `\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b`
- DB명 패턴: `localhost`, `postgres` 이외의 특정 DB명 (예: `myapp_db`, `prod_db` 등 프로젝트 전용 DB명)
- 사용자명 패턴: `postgres` 이외의 특정 사용자명 (예: `admin`, `dbadmin` 등 기본값이 아닌 사용자명)

허용되는 기본값(PASS):
- `"localhost"`, `"5432"`, `"postgres"`, `""`, `"http://localhost:11434"`, `"0.0.0.0"`

---

### [S03] env.get() / dict.get() 실제 인프라 기본값 탐지 — FAIL 시 차단

```bash
grep -rn --include="*.py" \
  -E "env\.get\s*\([^)]+,\s*['\"][^'\"]+['\"]" \
  <대상 파일들>
```

S02와 동일한 기준으로 기본값 판정.

---

### [S04] 실제 IP 주소 하드코딩 탐지 — FAIL 시 차단

```bash
grep -rn --include="*.py" \
  -E "\"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\"" \
  <대상 파일들>
```

**판정 기준**:
- `"127.0.0.1"`, `"0.0.0.0"` → PASS (루프백/와일드카드)
- 그 외 실제 IP → **FAIL**

---

### [S05] .env 파일 스테이징 여부 — FAIL 시 즉시 차단

```bash
git diff --cached --name-only | grep -E "(^|/)\.env(\.|$)"
```

`.env`, `.env.local`, `.env.production` 등이 staged → **FAIL**
`.env.example` → PASS

---

### [S06] 민감 파일 git 추적 여부 — FAIL 시 차단

```bash
git ls-files | grep -E "\.(env|pem|key|p12|pfx)$|credentials\.json|api_keys\.env|secrets\.json"
```

위 패턴 파일이 git에 추적 중 → **FAIL**

---

### [S07] .gitignore 필수 항목 누락 — FAIL 시 차단

```bash
cat .gitignore
```

아래 항목이 **모두** 포함되어야 PASS:
- `.env` 또는 `.env.*`
- `*.pem`
- `*.key`
- `credentials.json`
- `.claude/settings.local.json`

하나라도 없으면 → **FAIL**

---

### [S08] 하드코딩 로컬 경로 — WARNING (커밋 허용, 보고 필요)

```bash
grep -rn --include="*.py" \
  -E "\"C:/Users/[^\"]+\"|'C:/Users/[^']+'" \
  <대상 파일들>
```

**판정 기준**:
- 모듈 최상단 상수(`DEFAULT_*`, `MODEL_PATH` 등)이고 CLI 인자(`argparse`)로 덮어쓸 수 있으면 → **WARNING** (허용)
- 함수 내부 직접 사용 → **FAIL**

판정 방법: 해당 라인이 함수 안인지 확인
```bash
# 라인 주변 컨텍스트 확인 (-B5: 위 5줄)
grep -n "C:/Users/" <파일> | while read line; do
  lineno=$(echo "$line" | cut -d: -f1)
  # lineno 위 5줄에 'def ' 패턴이 있으면 함수 내부
done
```

---

## 전체 실행 스크립트

아래 스크립트를 Bash 도구로 실행한다. `TARGET_FILES`는 Step 0에서 수집한 파일 목록으로 대체한다.

```bash
#!/usr/bin/env bash
# 프로젝트 루트에서 실행 (git repo 루트)

echo "=== Security Audit 시작 ==="
echo "점검 시각: $(date '+%Y-%m-%d %H:%M')"
FAIL_COUNT=0
WARN_COUNT=0

# Step 0: 점검 대상 파일 수집
STAGED=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null)
MODIFIED=$(git diff HEAD --name-only --diff-filter=ACM 2>/dev/null)
TARGET_PY=$(echo -e "${STAGED}\n${MODIFIED}" | grep '\.py$' | sort -u)

if [ -z "$TARGET_PY" ]; then
  TARGET_PY=$(git diff HEAD~1 HEAD --name-only --diff-filter=ACM 2>/dev/null | grep '\.py$')
fi

echo "점검 파일: $(echo "$TARGET_PY" | grep -c '.py')개"
echo "---"

# S01: 하드코딩 자격증명
result=$(echo "$TARGET_PY" | xargs grep -n \
  -iE "(api_key|password|secret|token|passwd|pwd)\s*=\s*['\"][^'\"]{6,}['\"]" 2>/dev/null \
  | grep -viE "(os\.getenv|dotenv|config\.get|example|sample|test|placeholder)")
if [ -n "$result" ]; then
  echo "[S01 FAIL] 하드코딩 자격증명 탐지"
  echo "$result"
  FAIL_COUNT=$((FAIL_COUNT + 1))
else
  echo "[S01 PASS] 하드코딩 자격증명"
fi

# S02: os.getenv() 실제 인프라 기본값
result=$(echo "$TARGET_PY" | xargs grep -n \
  -E "os\.getenv\s*\([^)]+,\s*['\"][^'\"]+['\"]" 2>/dev/null \
  | grep -vE "(localhost|5432|postgres|http://localhost|0\.0\.0\.0|\"\")")
if [ -n "$result" ]; then
  echo "[S02 FAIL] os.getenv() 실제 인프라 기본값"
  echo "$result"
  FAIL_COUNT=$((FAIL_COUNT + 1))
else
  echo "[S02 PASS] os.getenv() 기본값"
fi

# S03: env.get() 실제 인프라 기본값
result=$(echo "$TARGET_PY" | xargs grep -n \
  -E "env\.get\s*\([^)]+,\s*['\"][^'\"]+['\"]" 2>/dev/null \
  | grep -vE "(localhost|5432|postgres|http://localhost|0\.0\.0\.0|\"\")")
if [ -n "$result" ]; then
  echo "[S03 FAIL] env.get() 실제 인프라 기본값"
  echo "$result"
  FAIL_COUNT=$((FAIL_COUNT + 1))
else
  echo "[S03 PASS] env.get() 기본값"
fi

# S04: 실제 IP 주소 하드코딩
result=$(echo "$TARGET_PY" | xargs grep -n \
  -E "\"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\"" 2>/dev/null \
  | grep -vE "(127\.0\.0\.1|0\.0\.0\.0)")
if [ -n "$result" ]; then
  echo "[S04 FAIL] 실제 IP 주소 하드코딩"
  echo "$result"
  FAIL_COUNT=$((FAIL_COUNT + 1))
else
  echo "[S04 PASS] IP 주소 하드코딩"
fi

# S05: .env 파일 스테이징
result=$(git diff --cached --name-only 2>/dev/null | grep -E "(^|/)\.env(\.|$)" | grep -v "\.example")
if [ -n "$result" ]; then
  echo "[S05 FAIL] .env 파일이 staged 상태"
  echo "$result"
  FAIL_COUNT=$((FAIL_COUNT + 1))
else
  echo "[S05 PASS] .env 스테이징"
fi

# S06: 민감 파일 git 추적
result=$(git ls-files 2>/dev/null | grep -E "\.(env|pem|key|p12|pfx)$|credentials\.json|api_keys\.env$|secrets\.json" | grep -v "\.example")
if [ -n "$result" ]; then
  echo "[S06 FAIL] 민감 파일 git 추적 중"
  echo "$result"
  FAIL_COUNT=$((FAIL_COUNT + 1))
else
  echo "[S06 PASS] 민감 파일 git 추적"
fi

# S07: .gitignore 필수 항목
GITIGNORE_FAIL=""
grep -q "\.env" .gitignore 2>/dev/null || GITIGNORE_FAIL="${GITIGNORE_FAIL} .env"
grep -q "\*\.pem" .gitignore 2>/dev/null || GITIGNORE_FAIL="${GITIGNORE_FAIL} *.pem"
grep -q "\*\.key" .gitignore 2>/dev/null || GITIGNORE_FAIL="${GITIGNORE_FAIL} *.key"
grep -q "credentials\.json" .gitignore 2>/dev/null || GITIGNORE_FAIL="${GITIGNORE_FAIL} credentials.json"
grep -q "settings\.local\.json" .gitignore 2>/dev/null || GITIGNORE_FAIL="${GITIGNORE_FAIL} settings.local.json"
if [ -n "$GITIGNORE_FAIL" ]; then
  echo "[S07 FAIL] .gitignore 누락 항목:${GITIGNORE_FAIL}"
  FAIL_COUNT=$((FAIL_COUNT + 1))
else
  echo "[S07 PASS] .gitignore 필수 항목"
fi

# S08: 하드코딩 로컬 경로 (WARNING)
result=$(echo "$TARGET_PY" | xargs grep -n \
  -E "\"C:/Users/[^\"]+\"|'C:/Users/[^']+'" 2>/dev/null)
if [ -n "$result" ]; then
  echo "[S08 WARN] 하드코딩 로컬 경로 — 상수+CLI오버라이드 확인 필요"
  echo "$result"
  WARN_COUNT=$((WARN_COUNT + 1))
else
  echo "[S08 PASS] 하드코딩 로컬 경로"
fi

echo ""
echo "=== Security Audit 완료 ==="
echo "FAIL: ${FAIL_COUNT}건 / WARN: ${WARN_COUNT}건"
if [ "$FAIL_COUNT" -gt 0 ]; then
  echo ">>> 커밋 차단 — FAIL 항목 수정 후 재실행"
else
  echo ">>> 커밋 진행 가능"
fi
```

---

## Orchestrator에 전달할 결과 형식

```
[Security Auditor 결과]
- 실행 시점: 코드 작성 후 / 커밋 직전
- 점검 파일: N개
- PASS: N건 / FAIL: N건 / WARN: N건

FAIL 항목:
- [S번호 FAIL] 설명
  위반 파일: path/to/file.py:라인번호
  위반 내용: (실제 값은 마스킹 — 예: api_key = "ab**...")

판단:
- FAIL 0건 → 커밋/실행 허용
- FAIL 1건 이상 → 즉시 차단, 수정 요청
- WARN만 존재 → 허용, 보고서에 기록
```

---

## 수정 가이드

### S01/S02/S03 위반 수정
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

### S05 위반 수정
```bash
git rm --cached .env
echo ".env" >> .gitignore
```

### S08 WARNING — 허용 조건 확인
```python
# WARNING 허용 (모듈 상단 상수 + CLI 인자 존재)
DEFAULT_TRAILERS_DIR = Path("C:/Users/daewo/DX_prod_2nd/trailers")  # ← 허용
parser.add_argument('--trailers-dir', default=str(DEFAULT_TRAILERS_DIR))

# FAIL로 격상 (함수 내부 직접 사용)
def process():
    path = Path("C:/Users/daewo/DX_prod_2nd/trailers")  # ← FAIL
```

---

## 주의사항

1. 점검 결과 출력에 실제 자격증명 값을 포함하지 않는다 (마스킹 처리)
2. S08 WARN 항목은 보고서 "보안 참고사항"에 기록하되 진행을 차단하지 않는다
3. S05/S06은 `git add` 이후 `git commit` 이전에만 유효하다
4. `.env.example`은 민감 정보 없이 키 이름만 포함된 경우 PASS
