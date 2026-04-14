PR 작성 전 커밋부터 PR 생성까지 전 과정을 자동으로 수행해줘.

---

## 1. 현재 브랜치 및 변경 파일 확인

```bash
git branch --show-current
git status
git diff --stat
```

현재 브랜치명을 파악하고, 변경된 파일이 **현재 브랜치 폴더** 내에 있는지 확인한다.
**다른 브랜치 폴더(예: API_Server/, Database/, Execution_Engine/, Frontend/)의 파일은 절대 스테이징하지 않는다.**

---

## 2. 보안 점검 (커밋 전 필수)

변경된 파일에 대해 아래 패턴을 스캔한다.

```bash
# 하드코딩된 자격증명 탐지
git diff | grep -E "(password|secret|api_key|token|host)\s*=\s*['\"][^'\"]{4,}"

# os.getenv 기본값에 실제 인프라 정보 탐지
git diff | grep -E "os\.getenv\(.+,\s*['\"]"
```

| 점검 항목 | 기준 |
|-----------|------|
| 하드코딩된 자격증명 | API 키, 비밀번호, 토큰 하드코딩 없어야 함 |
| os.getenv() 기본값 | 실제 IP, DB명, 사용자명 기본값 없어야 함 |
| .env 파일 포함 여부 | .gitignore에 .env 있는지 확인 |
| data/ 포함 여부 | .gitignore에 data/ 있는지 확인 |

- 탐지된 항목이 있으면 → **커밋 중단, 즉시 수정 요청**
- 이상 없으면 → "보안 점검 통과" 보고 후 계속 진행

---

## 3. 현재 브랜치 파일만 스테이징 및 커밋

미커밋 변경사항이 있는 경우에만 실행한다.

```bash
git add {현재 브랜치 폴더}/
git commit -m "..."
```

**커밋 금지 파일**: `.env`, `data/`, `*.parquet`, `*.pkl`, `*.pem`, `credentials.json`

---

## 4. base 브랜치 최신화

```bash
# 1) 원격 최신 상태 가져오기
git fetch origin

# 2) base 브랜치(main)와 현재 브랜치 간 diverge 여부 확인
git log HEAD..origin/main --oneline
git log origin/main..HEAD --oneline
```

- `origin/main`에 내 브랜치에 없는 커밋이 있으면 → **pull 먼저 수행**
- 충돌(conflict) 발생 시 → 사용자에게 충돌 파일 목록을 알리고 **중단**. 충돌 해결 후 재실행 요청.
- diverge 없으면 → 다음 단계로 진행

```bash
# diverge가 있는 경우에만 실행
git pull origin main
```

---

## 5. 변경사항 분석

```bash
# 베이스 대비 변경된 파일 목록
git diff --name-status origin/main...HEAD

# 커밋 히스토리
git log origin/main..HEAD --oneline
```

- 변경된 파일 수 및 목록 (추가/수정/삭제 구분)
- 각 커밋의 주요 내용 요약

---

## 6. 이전 PR 내용 확인

```bash
gh pr list --head {현재 브랜치} --state all --limit 1
gh pr view {PR번호} --json body
```

- 이전 PR이 있으면 body를 읽어 내용을 파악한다.
- 새 PR body 작성 시 이전 PR과 **중복되는 항목은 최신 내용으로 덮어써서 반영**, **새로 추가된 항목은 해당 섹션에 추가**한다.
- 이전 PR이 없으면 새로 작성한다.

---

## 7. PR 생성

위 분석 결과를 바탕으로 아래 형식으로 PR 본문을 작성하고 `gh pr create`를 실행한다.
PR base branch는 항상 `main`이다.

```
## 변경사항 요약
<!-- 변경된 파일별로 무엇을 왜 변경했는지 기술 (bullet 3개 이내) -->

## 사후 영향 평가
| 영향 범위 | 내용 | 조치 필요 여부 |
|-----------|------|---------------|
| 업스트림 의존성 | ... | Yes / No |
| 다운스트림 의존성 | ... | Yes / No |
| DB 스키마 변경 | ... | Yes / No |
| API 인터페이스 변경 | ... | Yes / No |

## 보안 평가
| 점검 항목 | 결과 |
|-----------|------|
| 하드코딩된 자격증명 | ✅/❌ |
| os.getenv() 기본값 인프라 노출 | ✅/❌ |
| .env, data/ gitignore 확인 | ✅/❌ |
| 외부 입력값 검증 | ✅/❌ |

## 테스트 체크리스트
- [ ] 로컬 실행 확인
- [ ] 주요 변경 함수 단위 테스트
- [ ] 관련 팀원에게 리뷰 요청

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

---

## ⛔ 절대 금지 규칙 (Claude 포함 모든 실행 주체)

**아래 행동은 사용자의 명시적 승인 없이 절대 실행하지 않는다.**

1. `git push origin main` — main 브랜치 직접 push 금지
2. PR 없이 main에 직접 merge 금지
3. PR 리뷰(Approve) 없이 merge 금지
4. 다른 브랜치 폴더 파일을 현재 브랜치 커밋에 포함 금지
5. PR 생성 과정에서 요청하지 않은 파일을 추가로 커밋·push 금지

**이 규칙은 사용자가 명시적으로 "push해줘", "merge해줘"라고 말하기 전까지 유효하다.**

> 위반 시: 즉시 중단하고 사용자에게 보고한다.
