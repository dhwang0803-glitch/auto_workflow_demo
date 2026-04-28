# IMPACT_ASSESSOR — 사후영향 평가 에이전트 (Frontend)

## 역할

PR 생성 전, Frontend 변경 사항이 다른 레이어 (API_Server / AI_Agent / Database) 에 미치는 영향을 분석하고 구조화된 **사후영향 평가 보고서** 를 생성한다.

---

## 트리거 조건

- PR 생성 직전
- API 클라이언트 / 라우트 / Zustand 스토어 인터페이스 변경이 포함된 모든 커밋

---

## 분석 절차

### Step 1. 변경 범위 파악

```bash
git diff main...HEAD --stat
git diff main...HEAD --name-only -- 'Frontend/**'
```

확인 항목:
- 변경된 파일 목록 (`src/app/`, `src/components/`, `src/lib/`, `src/store/`, `tests/`)
- 추가/삭제/수정 라인 수
- 새 라우트 / 컴포넌트 / 스토어 / 클라이언트 식별

### Step 1-b. 폴더 구조 변경 감지 (자동 🔴 HIGH 판정)

```bash
git diff main...HEAD --name-only -- 'Frontend/**' | \
  awk -F/ '$1=="Frontend"{print $2"/"$3}' | sort -u
```

아래 패턴이 감지되면 즉시 **🔴 HIGH** 확정:

| 감지 패턴 | 판정 | 이유 |
|-----------|------|------|
| `src/pages/` 신규 생성 | 🔴 HIGH | App Router 정책 위반 — `src/app/` 사용 |
| `src/services/` 신규 생성 | 🔴 HIGH | 클라이언트 위치는 `src/lib/` |
| `src/<arbitrary>/` (components/lib/store/providers/app 외) | 🔴 HIGH | 폴더 구조 규칙 위반 |
| 컨벤션 폴더 이름 변경 (예: `tests/` → `e2e/`) | 🔴 HIGH | 팀 합의 위반 |

**Frontend 컨벤션 폴더**:
- `src/app/`, `src/components/`, `src/lib/`, `src/store/`, `src/providers/`
- `public/`, `plans/`, `tests/`, `reports/`, `agents/`

---

### Step 2. 레이어별 영향 분석

#### Frontend 내부

- [ ] 새 라우트 추가 → `next build` 의 라우트 리스트에 등록되는지
- [ ] 새 Zustand 스토어 → 기존 스토어와 책임 중복 없는지
- [ ] data-testid 명명 일관 (`<scope>-<name>`)
- [ ] UI 텍스트 영어 (`feedback_hackathon_ui_english.md`)
- [ ] React Query 캐시 키 충돌 없는지 (`["workflows"]`, `["skills"]` 등)

#### API_Server 콘트랙트

- [ ] 호출하는 엔드포인트 path 가 main 의 `app/routers/*.py` 에 존재하는지
- [ ] 요청 body 가 `app/models/*.py` Pydantic 스키마와 일치하는지
- [ ] 응답 파싱이 nullable / optional 필드를 방어적으로 다루는지
- [ ] 새 엔드포인트 호출 → API_Server 측 PR 머지 선행됐는지 확인 (PR 본문에 SHA 명시)
- [ ] SSE 프레임 포맷 변경 → `composer.ts` dispatchFrame 와 일치

#### AI_Agent 콘트랙트 (간접 — API_Server 경유)

- [ ] skill bootstrap 흐름이 의존하는 AI_Agent 응답 shape (`AnswerResponse.draft.needs_clarification` 등) 의 nullable 처리

#### 보안 영향

- [ ] 새 `NEXT_PUBLIC_*` 환경변수에 시크릿 미포함 (클라이언트 번들 인라인됨)
- [ ] 자격증명 입력 폼이 redaction 후 즉시 초기화하는지
- [ ] `dangerouslySetInnerHTML` 사용 여부 (LLM 응답 raw 삽입 금지)

---

### Step 3. 리스크 등급 산정

| 등급 | 기준 | 대응 |
|------|------|------|
| 🔴 HIGH | 폴더 구조 위반 / API 콘트랙트 미스매치 / 시크릿 노출 가능성 | 사용자 검토 필수 |
| 🟡 MEDIUM | 새 라우트 / 새 스토어 / API 호출 추가 (콘트랙트는 일치) | 보고서 기록 후 머지 |
| 🟢 LOW | 텍스트 변경 / 스타일 / 컴포넌트 내부 리팩터 | 자동 머지 가능 |

### Step 4. 롤백 계획

- 새 라우트는 단순 삭제로 롤백 가능 (서버 상태 미변경)
- API_Server 콘트랙트 동시 변경된 경우 → API_Server 도 함께 롤백 필요

---

## 출력 형식 (PR Description 용)

```markdown
## 📊 사후영향 평가 (Frontend)

### 변경 범위
- **레이어**: Frontend (only)
- **변경 파일 수**: N개 (추가 X / 수정 Y)
- **새 라우트**: `/<path>` 또는 없음
- **새 스토어**: `<name>-store` 또는 없음

### 레이어별 영향

| 레이어 | 영향 여부 | 상세 |
|--------|-----------|------|
| 폴더 구조 규칙 | ✅ 준수 / 🔴 위반 | |
| API_Server 콘트랙트 | ✅ 일치 / 🟡 신규 호출 / 🔴 미스매치 | |
| AI_Agent 콘트랙트 (간접) | ✅ 영향 없음 / 🟡 응답 shape 의존 추가 | |
| 보안 (시크릿/XSS) | ✅ 준수 / 🔴 위반 | |

### 리스크 등급
🔴 HIGH / 🟡 MEDIUM / 🟢 LOW

**근거**: (한 줄)

### 의존 PR
- API_Server: PR #NNN (콘트랙트 머지 SHA: `<sha>`)
- AI_Agent: PR #NNN 또는 해당 없음

### 라우트 사이즈
| 경로 | 이전 | 이후 |
|------|------|------|
| `/skills/new` | 4.93 kB | 5.73 kB |
```

---

## 보안 점검 연계

IMPACT_ASSESSOR 는 보안 점검을 **직접 수행하지 않는다**. `SECURITY_AUDITOR` 에이전트가 담당.

---

## 제약 사항

- 분석 대상: `git diff main...HEAD -- 'Frontend/**'` 기준
- API 호출 실제 검증은 Tester Agent 의 Playwright (mock) + smoke (live) 가 담당
- `.env.local` 파일 읽기 금지
- 영향 분석은 **추론 기반** — 실제 통합 검증은 W2-8a 같은 단계
